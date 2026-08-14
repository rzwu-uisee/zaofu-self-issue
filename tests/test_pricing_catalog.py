from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import httpx
import pytest

from zf.core.cost.catalog import (
    PricingCatalogError,
    PricingCatalogStore,
    PricingCatalogSyncService,
)
from zf.core.cost.tracker import CostTracker


def test_exact_model_and_effective_time_resolve_replayable_rate(tmp_path: Path):
    store = PricingCatalogStore(tmp_path)

    resolved = store.resolve(
        provider="openai",
        model="openai/gpt-5.6-sol",
        occurred_at="2026-08-11T01:00:00Z",
        accounting_mode="api",
        request_input_tokens=100_000,
    )

    assert resolved is not None
    assert resolved.rate.rate_key == "openai:gpt-5.6-sol:standard:base"
    assert resolved.catalog_digest.startswith("sha256:")
    assert resolved.precision == "exact"
    assert store.resolve(
        provider="openai",
        model="gpt-5.6-sol-20260811",
        occurred_at="2026-08-11T01:00:00Z",
        accounting_mode="api",
        request_input_tokens=100_000,
    ) is None


def test_missing_context_dimension_is_partial_not_guessed_exact(tmp_path: Path):
    resolved = PricingCatalogStore(tmp_path).resolve(
        provider="openai",
        model="gpt-5.6-sol",
        accounting_mode="subscription",
    )

    assert resolved is not None
    assert resolved.precision == "partial"
    assert resolved.missing_dimensions == ("request_input_tokens",)
    assert resolved.estimate_kind == "api_equivalent"


def test_gpt_56_sol_token_classes_use_decimal_without_double_count(tmp_path: Path):
    tracker = CostTracker(tmp_path / "cost.jsonl")

    cost = tracker.record_usage(
        "dev",
        input_tokens=32_051_249,
        output_tokens=2_992_481,
        cache_read_tokens=772_663_552,
        model="gpt-5.6-sol",
        backend="codex",
        provider="openai",
        accounting_mode="subscription",
        request_input_tokens=100_000,
    )

    assert Decimal(str(cost)) == Decimal("636.362451")
    entry = json.loads((tmp_path / "cost.jsonl").read_text())
    assert entry["estimated_cost_usd"] == "636.362451"
    assert entry["pricing_catalog_digest"].startswith("sha256:")
    assert entry["pricing_formula_version"] == "deterministic-token-cost.v1"
    assert entry["estimate_kind"] == "api_equivalent"
    assert entry["cost_status"] == "exact"


def test_provider_reported_and_estimate_are_preserved_separately(tmp_path: Path):
    tracker = CostTracker(tmp_path / "cost.jsonl")

    cost = tracker.record_usage(
        "dev",
        input_tokens=1_000_000,
        output_tokens=1_000_000,
        model="claude-opus-4-8",
        backend="claude-code",
        provider_cost_usd=Decimal("42.125"),
    )

    entry = json.loads((tmp_path / "cost.jsonl").read_text())
    assert cost == 42.125
    assert entry["estimated_cost_usd"] == "30"
    assert entry["provider_reported_cost_usd"] == "42.125"
    assert entry["display_cost_kind"] == "provider_reported"


def test_unknown_model_is_unpriced_but_tokens_remain_visible(tmp_path: Path):
    tracker = CostTracker(tmp_path / "cost.jsonl")

    assert tracker.record_usage(
        "dev",
        input_tokens=100,
        output_tokens=20,
        model="future-model-that-is-not-catalogued",
        backend="codex",
    ) == 0

    totals = tracker.usage_totals()
    assert totals["total_tokens"] == 120
    assert totals["unpriced_entries"] == 1
    assert tracker.has_unpriced_usage() is True


def _remote_catalog() -> dict:
    path = Path(__file__).parents[1] / "src/zf/core/cost/default_pricing_catalog.json"
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["catalog_version"] = "remote-2026-08-11"
    raw["source_kind"] = "provider"
    raw["source_url"] = "https://pricing.example.test/catalog.json"
    raw.pop("digest", None)
    return raw


def test_remote_refresh_is_atomic_ttl_cached_and_last_known_good(tmp_path: Path):
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json=_remote_catalog())

    client = httpx.Client(transport=httpx.MockTransport(handler))
    store = PricingCatalogStore(tmp_path)
    service = PricingCatalogSyncService(
        store,
        url="https://pricing.example.test/catalog.json",
        ttl_seconds=86_400,
        client=client,
    )

    first = service.refresh()
    second = service.refresh()

    assert first["status"] == "updated"
    assert second["status"] == "fresh"
    assert calls == 1
    before = store.active_metadata()

    invalid_client = httpx.Client(transport=httpx.MockTransport(
        lambda request: httpx.Response(200, json={"broken": True})
    ))
    with pytest.raises(PricingCatalogError):
        PricingCatalogSyncService(
            store,
            url="https://pricing.example.test/catalog.json",
            client=invalid_client,
        ).refresh(force=True)
    assert store.active_metadata() == before


def test_legacy_cost_entry_remains_readable(tmp_path: Path):
    path = tmp_path / "cost.jsonl"
    path.write_text(
        '{"role":"dev","input_tokens":10,"output_tokens":5,'
        '"cost_usd":0.01,"ts":1}\n',
        encoding="utf-8",
    )

    summary = CostTracker(path).per_role_totals()["dev"]

    assert summary.total_usd == 0.01
    assert summary.unpriced_entries == 0
