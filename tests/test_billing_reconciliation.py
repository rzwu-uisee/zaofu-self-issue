from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import httpx
import pytest

from zf.core.cost.billing import (
    AnthropicBillingAdapter,
    BillingError,
    BillingReconciliationService,
    BillingReconciliationStore,
    OpenAIBillingAdapter,
)


START = "2026-08-01T00:00:00Z"
END = "2026-08-03T00:00:00Z"


def test_openai_multi_page_costs_normalize_bucket_and_project(tmp_path: Path):
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        page = request.url.params.get("page")
        if not page:
            return httpx.Response(200, json={
                "data": [{
                    "start_time": 1785542400,
                    "end_time": 1785628800,
                    "results": [{
                        "amount": {"value": "1.25", "currency": "usd"},
                        "project_id": "proj-a",
                        "line_item": "tokens",
                    }],
                }],
                "has_more": True,
                "next_page": "page-2",
            })
        return httpx.Response(200, json={
            "data": [{
                "start_time": 1785628800,
                "end_time": 1785715200,
                "results": [{
                    "amount": {"value": "2.75", "currency": "usd"},
                    "project_id": "proj-a",
                    "line_item": "tokens",
                }],
            }],
            "has_more": False,
        })

    client = httpx.Client(transport=httpx.MockTransport(handler))
    result = OpenAIBillingAdapter(
        api_key="secret-openai-admin-key",
        client=client,
    ).fetch(
        start=_dt(START),
        end=_dt(END),
    )

    assert result.page_count == 2
    assert [bucket.amount_usd for bucket in result.buckets] == ["1.25", "2.75"]
    assert all(bucket.attribution_precision == "project" for bucket in result.buckets)
    assert requests[1].url.params["page"] == "page-2"


def test_anthropic_cost_report_converts_cents_to_usd():
    client = httpx.Client(transport=httpx.MockTransport(
        lambda request: httpx.Response(200, json={
            "data": [{
                "starting_at": START,
                "ending_at": END,
                "results": [{
                    "amount": "1234",
                    "currency": "USD",
                    "workspace_id": "ws-a",
                    "description": "token usage",
                }],
            }],
            "has_more": False,
        })
    ))

    result = AnthropicBillingAdapter(
        api_key="secret-anthropic-admin-key",
        client=client,
    ).fetch(start=_dt(START), end=_dt(END))

    assert result.buckets[0].amount_usd == "12.34"
    assert result.buckets[0].attribution_precision == "workspace"


def test_missing_admin_key_fails_before_store_write(tmp_path: Path):
    store = BillingReconciliationStore(tmp_path)

    with pytest.raises(BillingError, match="admin key is required"):
        BillingReconciliationService(store).reconcile(
            provider="openai",
            accounting_mode="api",
            start=START,
            end=END,
            api_key="",
        )

    assert not store.root.exists()


def test_subscription_is_not_available_without_calling_provider(tmp_path: Path):
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(500)

    result = BillingReconciliationService(
        BillingReconciliationStore(tmp_path)
    ).reconcile(
        provider="openai",
        accounting_mode="subscription",
        start=START,
        end=END,
        estimated_usd=Decimal("6.5"),
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    assert calls == 0
    assert result["status"] == "not_available"
    assert result["estimated_usd"] == "6.5"
    assert result["billed_usd"] is None


def test_reconcile_is_idempotent_and_does_not_persist_key(tmp_path: Path):
    secret = "secret-openai-admin-key"
    client = httpx.Client(transport=httpx.MockTransport(
        lambda request: httpx.Response(200, json={
            "data": [{
                "amount": {"value": "5", "currency": "usd"},
                "project_id": "proj-a",
                "start_time": 1785542400,
                "end_time": 1785715200,
            }],
            "has_more": False,
        })
    ))
    service = BillingReconciliationService(
        BillingReconciliationStore(tmp_path)
    )

    first = service.reconcile(
        provider="openai",
        accounting_mode="api",
        start=START,
        end=END,
        project_id="zaofu-a",
        estimated_usd=Decimal("4.5"),
        api_key=secret,
        client=client,
    )
    second = service.reconcile(
        provider="openai",
        accounting_mode="api",
        start=START,
        end=END,
        project_id="zaofu-a",
        estimated_usd=Decimal("4.5"),
        api_key=secret,
        client=client,
    )

    assert first["ref"] == second["ref"]
    assert first["billed_usd"] == "5"
    assert first["variance_usd"] == "0.5"
    body = Path(first["ref"]).read_text(encoding="utf-8")
    assert secret not in body
    assert len(list((tmp_path / "cost/reconciliation/openai").glob("*.json"))) == 1
    assert json.loads(body)["source_endpoint"].endswith("/organization/costs")


def _dt(value: str):
    from datetime import datetime

    return datetime.fromisoformat(value.replace("Z", "+00:00"))
