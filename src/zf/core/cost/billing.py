"""Provider organization billing adapters and deterministic reconciliation."""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Mapping, Protocol

import httpx

from zf.core.state.atomic_io import atomic_write_text


class BillingError(RuntimeError):
    """A sanitized operator-facing billing failure."""


def _decimal(value: object, *, field_name: str) -> Decimal:
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise BillingError(f"invalid decimal in {field_name}") from exc
    if not parsed.is_finite() or parsed < 0:
        raise BillingError(f"invalid decimal in {field_name}")
    return parsed


def _decimal_text(value: Decimal) -> str:
    return format(value.normalize(), "f") if value else "0"


def _utc_timestamp(value: str, *, field_name: str) -> datetime:
    text = str(value or "").strip()
    if not text:
        raise BillingError(f"{field_name} is required")
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise BillingError(f"{field_name} must be RFC3339") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


@dataclass(frozen=True)
class BillingBucket:
    provider: str
    start: str
    end: str
    amount_usd: str
    attribution_precision: str
    dimensions: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class BillingFetchResult:
    provider: str
    source_endpoint: str
    buckets: tuple[BillingBucket, ...]
    page_count: int


class BillingAdapter(Protocol):
    provider: str

    def fetch(self, *, start: datetime, end: datetime) -> BillingFetchResult:
        ...


class _HttpBillingAdapter:
    provider = ""
    endpoint = ""

    def __init__(
        self,
        *,
        api_key: str,
        client: httpx.Client | None = None,
        timeout_seconds: float = 20.0,
    ) -> None:
        self.api_key = str(api_key or "").strip()
        if not self.api_key:
            raise BillingError(f"{self.provider} billing admin key is required")
        self.client = client
        self.timeout_seconds = max(0.1, float(timeout_seconds))

    def _get_pages(
        self,
        *,
        params: dict[str, Any],
        headers: dict[str, str],
    ) -> list[Mapping[str, Any]]:
        owns_client = self.client is None
        client = self.client or httpx.Client(timeout=self.timeout_seconds)
        pages: list[Mapping[str, Any]] = []
        next_page = ""
        try:
            while True:
                request_params = dict(params)
                if next_page:
                    request_params["page"] = next_page
                try:
                    response = client.get(
                        self.endpoint,
                        params=request_params,
                        headers=headers,
                    )
                    response.raise_for_status()
                    body = response.json()
                except (httpx.HTTPError, ValueError) as exc:
                    # Response bodies and headers can echo credentials or
                    # provider internals. Only expose the failure class.
                    raise BillingError(
                        f"{self.provider} billing request failed: "
                        f"{type(exc).__name__}"
                    ) from exc
                if not isinstance(body, Mapping):
                    raise BillingError(
                        f"{self.provider} billing response must be an object"
                    )
                pages.append(body)
                next_page = str(
                    body.get("next_page") or body.get("next_page_token") or ""
                )
                if not body.get("has_more") or not next_page:
                    break
        finally:
            if owns_client:
                client.close()
        return pages


class OpenAIBillingAdapter(_HttpBillingAdapter):
    provider = "openai"
    endpoint = "https://api.openai.com/v1/organization/costs"

    def fetch(self, *, start: datetime, end: datetime) -> BillingFetchResult:
        pages = self._get_pages(
            params={
                "start_time": int(start.timestamp()),
                "end_time": int(end.timestamp()),
                "bucket_width": "1d",
                "limit": 180,
            },
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Accept": "application/json",
            },
        )
        buckets: list[BillingBucket] = []
        for page in pages:
            for bucket in page.get("data", []) or []:
                if not isinstance(bucket, Mapping):
                    continue
                results = bucket.get("results")
                rows = results if isinstance(results, list) else [bucket]
                for row in rows:
                    if not isinstance(row, Mapping):
                        continue
                    amount = row.get("amount")
                    amount_value = (
                        amount.get("value")
                        if isinstance(amount, Mapping)
                        else amount
                    )
                    dimensions = _dimensions(
                        row,
                        "project_id",
                        "line_item",
                        "organization_id",
                    )
                    buckets.append(BillingBucket(
                        provider=self.provider,
                        start=_epoch_or_text(
                            bucket.get("start_time") or row.get("start_time")
                        ),
                        end=_epoch_or_text(
                            bucket.get("end_time") or row.get("end_time")
                        ),
                        amount_usd=_decimal_text(_decimal(
                            amount_value or 0,
                            field_name="openai.amount.value",
                        )),
                        attribution_precision=(
                            "project" if dimensions.get("project_id")
                            else "organization"
                        ),
                        dimensions=dimensions,
                    ))
        return BillingFetchResult(
            provider=self.provider,
            source_endpoint=self.endpoint,
            buckets=tuple(buckets),
            page_count=len(pages),
        )


class AnthropicBillingAdapter(_HttpBillingAdapter):
    provider = "anthropic"
    endpoint = "https://api.anthropic.com/v1/organizations/cost_report"

    def fetch(self, *, start: datetime, end: datetime) -> BillingFetchResult:
        pages = self._get_pages(
            params={
                "starting_at": start.isoformat().replace("+00:00", "Z"),
                "ending_at": end.isoformat().replace("+00:00", "Z"),
                "bucket_width": "1d",
                "limit": 31,
            },
            headers={
                "x-api-key": self.api_key,
                "anthropic-version": "2023-06-01",
                "Accept": "application/json",
            },
        )
        buckets: list[BillingBucket] = []
        for page in pages:
            for bucket in page.get("data", []) or []:
                if not isinstance(bucket, Mapping):
                    continue
                rows = bucket.get("results") or []
                if not isinstance(rows, list):
                    continue
                for row in rows:
                    if not isinstance(row, Mapping):
                        continue
                    # Anthropic cost-report amounts are denominated in cents.
                    amount_usd = _decimal(
                        row.get("amount") or 0,
                        field_name="anthropic.amount",
                    ) / Decimal(100)
                    dimensions = _dimensions(
                        row,
                        "workspace_id",
                        "description",
                        "cost_type",
                        "model",
                        "service_tier",
                        "context_window",
                    )
                    buckets.append(BillingBucket(
                        provider=self.provider,
                        start=str(bucket.get("starting_at") or ""),
                        end=str(bucket.get("ending_at") or ""),
                        amount_usd=_decimal_text(amount_usd),
                        attribution_precision=(
                            "workspace" if dimensions.get("workspace_id")
                            else "organization"
                        ),
                        dimensions=dimensions,
                    ))
        return BillingFetchResult(
            provider=self.provider,
            source_endpoint=self.endpoint,
            buckets=tuple(buckets),
            page_count=len(pages),
        )


class BillingReconciliationStore:
    def __init__(self, state_dir: Path) -> None:
        self.root = Path(state_dir) / "cost" / "reconciliation"

    def persist(self, payload: Mapping[str, Any]) -> Path:
        identity = {
            key: payload.get(key)
            for key in (
                "provider",
                "accounting_mode",
                "window_start",
                "window_end",
                "project_id",
            )
        }
        digest = hashlib.sha256(json.dumps(
            identity,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")).hexdigest()[:24]
        provider = str(payload.get("provider") or "unknown")
        path = self.root / provider / f"{digest}.json"
        atomic_write_text(path, json.dumps(
            dict(payload),
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
        ) + "\n")
        return path

    def latest(self, provider: str = "") -> dict[str, Any]:
        paths = sorted(
            (self.root / provider).glob("*.json")
            if provider
            else self.root.glob("*/*.json"),
            key=lambda path: path.stat().st_mtime_ns,
            reverse=True,
        )
        for path in paths:
            try:
                value = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if isinstance(value, dict):
                return value
        return {}


class BillingReconciliationService:
    def __init__(self, store: BillingReconciliationStore) -> None:
        self.store = store

    def reconcile(
        self,
        *,
        provider: str,
        accounting_mode: str,
        start: str,
        end: str,
        project_id: str = "",
        estimated_usd: Decimal | None = None,
        api_key: str = "",
        client: httpx.Client | None = None,
    ) -> dict[str, Any]:
        provider_name = str(provider or "").strip().lower()
        mode = str(accounting_mode or "unknown").strip().lower()
        start_at = _utc_timestamp(start, field_name="start")
        end_at = _utc_timestamp(end, field_name="end")
        if end_at <= start_at:
            raise BillingError("end must be after start")
        base = {
            "schema_version": "billing-reconciliation.v1",
            "provider": provider_name,
            "accounting_mode": mode,
            "window_start": start_at.isoformat(),
            "window_end": end_at.isoformat(),
            "project_id": project_id,
            "fetched_at": datetime.now(timezone.utc).isoformat(),
        }
        if mode == "subscription":
            payload = {
                **base,
                "status": "not_available",
                "reason": "subscription_route_has_no_organization_api_bill",
                "attribution_precision": "none",
                "billed_usd": None,
                "estimated_usd": (
                    _decimal_text(estimated_usd)
                    if estimated_usd is not None else None
                ),
                "variance_usd": None,
                "buckets": [],
            }
            path = self.store.persist(payload)
            return {**payload, "ref": str(path)}
        if mode not in {"api", "enterprise"}:
            raise BillingError(
                "accounting_mode must be api, enterprise, or subscription"
            )
        adapter = _adapter(
            provider_name,
            api_key=api_key,
            client=client,
        )
        fetched = adapter.fetch(start=start_at, end=end_at)
        billed = sum(
            (_decimal(bucket.amount_usd, field_name="bucket.amount_usd")
             for bucket in fetched.buckets),
            Decimal("0"),
        )
        variance = billed - estimated_usd if estimated_usd is not None else None
        precisions = {bucket.attribution_precision for bucket in fetched.buckets}
        payload = {
            **base,
            "status": "reconciled",
            "source_endpoint": fetched.source_endpoint,
            "page_count": fetched.page_count,
            "attribution_precision": (
                next(iter(precisions)) if len(precisions) == 1 else "mixed"
            ),
            "billed_usd": _decimal_text(billed),
            "estimated_usd": (
                _decimal_text(estimated_usd)
                if estimated_usd is not None else None
            ),
            "variance_usd": (
                _decimal_text(variance) if variance is not None else None
            ),
            "buckets": [asdict(bucket) for bucket in fetched.buckets],
        }
        path = self.store.persist(payload)
        return {**payload, "ref": str(path)}


def admin_key_from_environment(provider: str) -> str:
    name = {
        "openai": "OPENAI_ADMIN_KEY",
        "anthropic": "ANTHROPIC_ADMIN_API_KEY",
    }.get(str(provider or "").strip().lower(), "")
    return os.environ.get(name, "").strip() if name else ""


def _adapter(
    provider: str,
    *,
    api_key: str,
    client: httpx.Client | None,
) -> BillingAdapter:
    if provider == "openai":
        return OpenAIBillingAdapter(api_key=api_key, client=client)
    if provider == "anthropic":
        return AnthropicBillingAdapter(api_key=api_key, client=client)
    raise BillingError(f"unsupported billing provider: {provider or '<empty>'}")


def _dimensions(row: Mapping[str, Any], *names: str) -> dict[str, str]:
    return {
        name: str(row.get(name))
        for name in names
        if row.get(name) not in (None, "")
    }


def _epoch_or_text(value: object) -> str:
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value, tz=timezone.utc).isoformat()
    return str(value or "")


__all__ = [
    "AnthropicBillingAdapter",
    "BillingBucket",
    "BillingError",
    "BillingFetchResult",
    "BillingReconciliationService",
    "BillingReconciliationStore",
    "OpenAIBillingAdapter",
    "admin_key_from_environment",
]
