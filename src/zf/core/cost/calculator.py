"""Pure deterministic calculation over usage classes and a catalog snapshot."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Mapping

from zf.core.cost.catalog import (
    PricingCatalogStore,
    RateResolution,
    provider_for_backend,
)


@dataclass(frozen=True)
class CostCalculation:
    provider: str
    estimate: Decimal | None
    provider_reported: Decimal | None
    display_cost: Decimal
    cost_source: str
    display_kind: str
    cost_status: str
    resolution: RateResolution | None


def calculate_usage_cost(
    *,
    catalog_store: PricingCatalogStore,
    legacy_rates: Mapping[str, Mapping[str, object]] | None,
    provider: str,
    backend: str,
    model: str,
    accounting_mode: str,
    occurred_at: str,
    service_tier: str,
    request_input_tokens: int | None,
    input_tokens: int,
    output_tokens: int,
    cache_creation_tokens: int,
    cache_creation_1h_tokens: int,
    cache_read_tokens: int,
    provider_cost_usd: float | None,
) -> CostCalculation:
    provider_name = str(provider or "").strip().lower() or (
        provider_for_backend(backend, model=model)
    )
    estimate: Decimal | None = None
    resolution = None
    if legacy_rates is not None and model in legacy_rates:
        rate = legacy_rates[model]
        estimate = (
            Decimal(input_tokens) * Decimal(str(rate["input"]))
            + Decimal(output_tokens) * Decimal(str(rate["output"]))
        ) / Decimal(1_000_000)
    else:
        resolution = catalog_store.resolve(
            provider=provider_name,
            model=model,
            occurred_at=occurred_at,
            accounting_mode=accounting_mode,
            service_tier=service_tier,
            request_input_tokens=request_input_tokens,
        )
        if resolution is not None:
            units = resolution.rate.units
            estimate = (
                Decimal(input_tokens) * units.fresh_input
                + Decimal(output_tokens) * units.output
                + Decimal(cache_creation_tokens) * units.cache_write_5m
                + Decimal(cache_creation_1h_tokens) * units.cache_write_1h
                + Decimal(cache_read_tokens) * units.cache_read
            ) / Decimal(1_000_000)
    reported = (
        Decimal(str(provider_cost_usd))
        if provider_cost_usd is not None and provider_cost_usd > 0
        else None
    )
    if reported is not None:
        return CostCalculation(
            provider=provider_name,
            estimate=estimate,
            provider_reported=reported,
            display_cost=reported,
            cost_source="provider_reported",
            display_kind="provider_reported",
            cost_status="reported",
            resolution=resolution,
        )
    if estimate is not None:
        return CostCalculation(
            provider=provider_name,
            estimate=estimate,
            provider_reported=None,
            display_cost=estimate,
            cost_source="catalog_estimate",
            display_kind="estimated",
            cost_status=(
                resolution.precision if resolution is not None else "exact"
            ),
            resolution=resolution,
        )
    return CostCalculation(
        provider=provider_name,
        estimate=None,
        provider_reported=None,
        display_cost=Decimal("0"),
        cost_source="unpriced",
        display_kind="unpriced",
        cost_status="unpriced",
        resolution=None,
    )


__all__ = ["CostCalculation", "calculate_usage_cost"]
