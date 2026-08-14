"""Versioned pricing catalog, exact rate resolution, and cached refresh."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import urlparse

import httpx

from zf.core.state.atomic_io import atomic_write_text


CATALOG_SCHEMA = "pricing-catalog.v1"
_BUNDLED_CATALOG = Path(__file__).with_name("default_pricing_catalog.json")
_VALID_ACCOUNTING_MODES = frozenset({
    "api",
    "subscription",
    "enterprise",
    "unknown",
})


class PricingCatalogError(ValueError):
    """Raised when a catalog cannot be trusted or resolved."""


def _decimal(value: object, *, field: str) -> Decimal:
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise PricingCatalogError(f"{field} must be a decimal") from exc
    if not parsed.is_finite() or parsed < 0:
        raise PricingCatalogError(f"{field} must be finite and non-negative")
    if parsed > Decimal("100000"):
        raise PricingCatalogError(f"{field} exceeds the safety ceiling")
    return parsed


def _timestamp(value: object, *, field: str) -> datetime:
    text = str(value or "").strip()
    if not text:
        raise PricingCatalogError(f"{field} is required")
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise PricingCatalogError(f"{field} must be RFC3339") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _normalized_model(value: str) -> str:
    model = str(value or "").strip().lower().replace(".", "-")
    if "/" in model:
        model = model.rsplit("/", 1)[1]
    return model


def _catalog_digest(payload: Mapping[str, Any]) -> str:
    body = dict(payload)
    body.pop("digest", None)
    encoded = json.dumps(
        body,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True)
class PriceUnits:
    fresh_input: Decimal
    output: Decimal
    cache_read: Decimal = Decimal("0")
    cache_write_5m: Decimal = Decimal("0")
    cache_write_1h: Decimal = Decimal("0")


@dataclass(frozen=True)
class PricingRate:
    provider: str
    rate_key: str
    model_ids: tuple[str, ...]
    model_aliases: tuple[str, ...]
    accounting_modes: tuple[str, ...]
    service_tier: str
    context_input_gt: int | None
    context_input_lte: int | None
    units: PriceUnits

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "PricingRate":
        provider = str(raw.get("provider") or "").strip().lower()
        rate_key = str(raw.get("rate_key") or "").strip()
        model_ids = tuple(
            _normalized_model(item)
            for item in raw.get("model_ids", [])
            if str(item or "").strip()
        )
        aliases = tuple(
            _normalized_model(item)
            for item in raw.get("model_aliases", [])
            if str(item or "").strip()
        )
        accounting_modes = tuple(
            str(item or "").strip().lower()
            for item in raw.get("accounting_modes", ["api"])
            if str(item or "").strip()
        )
        if not provider or not rate_key or not model_ids:
            raise PricingCatalogError(
                "each rate requires provider, rate_key, and model_ids"
            )
        if not accounting_modes or any(
            item not in _VALID_ACCOUNTING_MODES for item in accounting_modes
        ):
            raise PricingCatalogError(
                f"{rate_key}: invalid accounting_modes"
            )
        conditions = raw.get("conditions") or {}
        if not isinstance(conditions, Mapping):
            raise PricingCatalogError(f"{rate_key}: conditions must be object")
        units = raw.get("units") or {}
        if not isinstance(units, Mapping):
            raise PricingCatalogError(f"{rate_key}: units must be object")
        return cls(
            provider=provider,
            rate_key=rate_key,
            model_ids=model_ids,
            model_aliases=aliases,
            accounting_modes=accounting_modes,
            service_tier=str(
                conditions.get("service_tier") or "standard"
            ).strip().lower(),
            context_input_gt=_optional_non_negative_int(
                conditions.get("context_input_gt"),
                field=f"{rate_key}.context_input_gt",
            ),
            context_input_lte=_optional_non_negative_int(
                conditions.get("context_input_lte"),
                field=f"{rate_key}.context_input_lte",
            ),
            units=PriceUnits(
                fresh_input=_decimal(
                    units.get("fresh_input_per_mtok", 0),
                    field=f"{rate_key}.fresh_input_per_mtok",
                ),
                output=_decimal(
                    units.get("output_per_mtok", 0),
                    field=f"{rate_key}.output_per_mtok",
                ),
                cache_read=_decimal(
                    units.get("cache_read_per_mtok", 0),
                    field=f"{rate_key}.cache_read_per_mtok",
                ),
                cache_write_5m=_decimal(
                    units.get("cache_write_5m_per_mtok", 0),
                    field=f"{rate_key}.cache_write_5m_per_mtok",
                ),
                cache_write_1h=_decimal(
                    units.get("cache_write_1h_per_mtok", 0),
                    field=f"{rate_key}.cache_write_1h_per_mtok",
                ),
            ),
        )

    def matches_model(self, model: str) -> bool:
        normalized = _normalized_model(model)
        return normalized in {*self.model_ids, *self.model_aliases}


def _optional_non_negative_int(value: object, *, field: str) -> int | None:
    if value in (None, ""):
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise PricingCatalogError(f"{field} must be an integer") from exc
    if parsed < 0:
        raise PricingCatalogError(f"{field} must be non-negative")
    return parsed


@dataclass(frozen=True)
class PricingCatalog:
    catalog_version: str
    currency: str
    source_kind: str
    source_url: str
    fetched_at: datetime
    effective_from: datetime
    effective_to: datetime | None
    digest: str
    rates: tuple[PricingRate, ...]
    raw: Mapping[str, Any]

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "PricingCatalog":
        if raw.get("schema_version") != CATALOG_SCHEMA:
            raise PricingCatalogError(
                f"schema_version must be {CATALOG_SCHEMA}"
            )
        version = str(raw.get("catalog_version") or "").strip()
        if not version:
            raise PricingCatalogError("catalog_version is required")
        currency = str(raw.get("currency") or "").strip().upper()
        if currency != "USD":
            raise PricingCatalogError("only USD catalogs are supported")
        rates_raw = raw.get("rates")
        if not isinstance(rates_raw, list) or not rates_raw:
            raise PricingCatalogError("rates must be a non-empty list")
        rates = tuple(
            PricingRate.from_dict(item)
            for item in rates_raw
            if isinstance(item, Mapping)
        )
        if len(rates) != len(rates_raw):
            raise PricingCatalogError("every rates item must be an object")
        keys = [rate.rate_key for rate in rates]
        if len(keys) != len(set(keys)):
            raise PricingCatalogError("rate_key values must be unique")
        computed_digest = _catalog_digest(raw)
        supplied_digest = str(raw.get("digest") or "").strip()
        if supplied_digest and supplied_digest != computed_digest:
            raise PricingCatalogError("catalog digest does not match payload")
        effective_to = raw.get("effective_to")
        return cls(
            catalog_version=version,
            currency=currency,
            source_kind=str(raw.get("source_kind") or "unknown").strip(),
            source_url=str(raw.get("source_url") or "").strip(),
            fetched_at=_timestamp(raw.get("fetched_at"), field="fetched_at"),
            effective_from=_timestamp(
                raw.get("effective_from"), field="effective_from"
            ),
            effective_to=(
                _timestamp(effective_to, field="effective_to")
                if effective_to
                else None
            ),
            digest=computed_digest,
            rates=rates,
            raw={**dict(raw), "digest": computed_digest},
        )

    def effective_at(self, at: datetime) -> bool:
        return self.effective_from <= at and (
            self.effective_to is None or at < self.effective_to
        )


@dataclass(frozen=True)
class RateResolution:
    rate: PricingRate
    catalog_version: str
    catalog_digest: str
    pricing_effective_at: str
    precision: str
    missing_dimensions: tuple[str, ...]
    estimate_kind: str


class PricingCatalogStore:
    """Load bundled + state-dir snapshots and resolve by effective time."""

    def __init__(self, state_dir: Path) -> None:
        self.state_dir = Path(state_dir)
        self.catalog_dir = self.state_dir / "cost" / "pricing-catalog"
        self.snapshot_dir = self.catalog_dir / "snapshots"
        self.active_path = self.catalog_dir / "active.json"

    def load_catalogs(self) -> list[PricingCatalog]:
        catalogs: dict[str, PricingCatalog] = {}
        for path in (_BUNDLED_CATALOG, *sorted(self.snapshot_dir.glob("*.json"))):
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
                catalog = PricingCatalog.from_dict(raw)
            except (OSError, json.JSONDecodeError, PricingCatalogError):
                if path == _BUNDLED_CATALOG:
                    raise
                continue
            catalogs[catalog.digest] = catalog
        return sorted(
            catalogs.values(),
            key=lambda item: (item.effective_from, item.fetched_at),
        )

    def resolve(
        self,
        *,
        provider: str,
        model: str,
        occurred_at: str = "",
        accounting_mode: str = "unknown",
        service_tier: str = "standard",
        request_input_tokens: int | None = None,
    ) -> RateResolution | None:
        provider = str(provider or "").strip().lower()
        model = str(model or "").strip()
        if not provider or not model:
            return None
        at = (
            _timestamp(occurred_at, field="occurred_at")
            if occurred_at
            else datetime.now(timezone.utc)
        )
        mode = str(accounting_mode or "unknown").strip().lower()
        if mode not in _VALID_ACCOUNTING_MODES:
            mode = "unknown"
        tier = str(service_tier or "standard").strip().lower()
        catalogs = [item for item in self.load_catalogs() if item.effective_at(at)]
        if not catalogs:
            return None
        catalog = catalogs[-1]
        candidates = [
            rate
            for rate in catalog.rates
            if rate.provider == provider
            and mode in rate.accounting_modes
            and rate.service_tier == tier
            and rate.matches_model(model)
        ]
        if not candidates:
            return None
        selected, missing = _select_context_rate(
            candidates,
            request_input_tokens=request_input_tokens,
        )
        if selected is None:
            return None
        missing_dimensions = tuple(missing)
        return RateResolution(
            rate=selected,
            catalog_version=catalog.catalog_version,
            catalog_digest=catalog.digest,
            pricing_effective_at=catalog.effective_from.isoformat(),
            precision="partial" if missing_dimensions else "exact",
            missing_dimensions=missing_dimensions,
            estimate_kind=(
                "api_list_price" if mode == "api" else "api_equivalent"
            ),
        )

    def persist(self, raw: Mapping[str, Any]) -> PricingCatalog:
        catalog = PricingCatalog.from_dict(raw)
        digest_name = catalog.digest.removeprefix("sha256:") + ".json"
        snapshot = self.snapshot_dir / digest_name
        body = json.dumps(
            catalog.raw,
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
        ) + "\n"
        atomic_write_text(snapshot, body)
        atomic_write_text(
            self.active_path,
            json.dumps(
                {
                    "schema_version": "pricing-catalog-active.v1",
                    "catalog_version": catalog.catalog_version,
                    "digest": catalog.digest,
                    "fetched_at": catalog.fetched_at.isoformat(),
                    "synced_at": datetime.now(timezone.utc).isoformat(),
                    "effective_from": catalog.effective_from.isoformat(),
                    "source_url": catalog.source_url,
                    "snapshot": str(snapshot.relative_to(self.state_dir)),
                },
                ensure_ascii=False,
                sort_keys=True,
                indent=2,
            ) + "\n",
        )
        return catalog

    def active_metadata(self) -> dict[str, Any]:
        try:
            value = json.loads(self.active_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        return value if isinstance(value, dict) else {}


def _select_context_rate(
    candidates: list[PricingRate],
    *,
    request_input_tokens: int | None,
) -> tuple[PricingRate | None, list[str]]:
    if request_input_tokens is not None:
        for rate in candidates:
            if (
                rate.context_input_gt is not None
                and request_input_tokens <= rate.context_input_gt
            ):
                continue
            if (
                rate.context_input_lte is not None
                and request_input_tokens > rate.context_input_lte
            ):
                continue
            return rate, []
        return None, []
    unconditional = [
        rate
        for rate in candidates
        if rate.context_input_gt is None and rate.context_input_lte is None
    ]
    if unconditional:
        return unconditional[0], []
    base = sorted(
        candidates,
        key=lambda rate: (
            rate.context_input_gt is not None,
            rate.context_input_lte or 2**63,
        ),
    )[0]
    return base, ["request_input_tokens"]


class PricingCatalogSyncService:
    """Fetch one explicitly configured remote catalog into last-known-good."""

    def __init__(
        self,
        store: PricingCatalogStore,
        *,
        url: str,
        ttl_seconds: int = 86_400,
        timeout_seconds: float = 10.0,
        client: httpx.Client | None = None,
    ) -> None:
        self.store = store
        self.url = str(url or "").strip()
        self.ttl_seconds = max(60, int(ttl_seconds))
        self.timeout_seconds = max(0.1, float(timeout_seconds))
        self.client = client

    def refresh(self, *, force: bool = False) -> dict[str, Any]:
        if not self.url:
            return {"status": "disabled"}
        parsed = urlparse(self.url)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
        ):
            raise PricingCatalogError(
                "pricing catalog URL must be credential-free HTTPS"
            )
        metadata = self.store.active_metadata()
        if not force and not self._due(metadata):
            return {"status": "fresh", **metadata}
        owns_client = self.client is None
        client = self.client or httpx.Client(timeout=self.timeout_seconds)
        try:
            response = client.get(
                self.url,
                headers={"Accept": "application/json"},
            )
            response.raise_for_status()
            raw = response.json()
        except (httpx.HTTPError, json.JSONDecodeError, ValueError) as exc:
            raise PricingCatalogError(
                f"pricing catalog refresh failed: {type(exc).__name__}"
            ) from exc
        finally:
            if owns_client:
                client.close()
        if not isinstance(raw, Mapping):
            raise PricingCatalogError("pricing catalog response must be object")
        catalog = self.store.persist(raw)
        return {
            "status": "updated",
            "catalog_version": catalog.catalog_version,
            "digest": catalog.digest,
            "effective_from": catalog.effective_from.isoformat(),
        }

    def _due(self, metadata: Mapping[str, Any]) -> bool:
        if not metadata:
            return True
        try:
            fetched = _timestamp(
                metadata.get("synced_at") or metadata.get("fetched_at"),
                field="synced_at",
            )
        except PricingCatalogError:
            return True
        age = datetime.now(timezone.utc) - fetched
        return age.total_seconds() >= self.ttl_seconds


def provider_for_backend(backend: str, *, model: str = "") -> str:
    """Map Codex/OpenAI and Claude/Anthropic backend identities."""

    value = str(backend or "").strip().lower()
    normalized = _normalized_model(model)
    # ``default`` is the explicit legacy compatibility model. Runtime
    # receipts preserve a missing observed model as an empty string, so this
    # branch cannot hide an unknown provider model.
    if normalized == "default":
        return "legacy"
    if value.startswith("codex"):
        return "openai"
    if value.startswith("claude"):
        return "anthropic"
    if normalized.startswith("gpt-") or normalized.startswith("o"):
        return "openai"
    if normalized.startswith("claude-") or normalized in {
        "opus",
        "sonnet",
        "haiku",
    }:
        return "anthropic"
    return ""


__all__ = [
    "CATALOG_SCHEMA",
    "PriceUnits",
    "PricingCatalog",
    "PricingCatalogError",
    "PricingCatalogStore",
    "PricingCatalogSyncService",
    "PricingRate",
    "RateResolution",
    "provider_for_backend",
]
