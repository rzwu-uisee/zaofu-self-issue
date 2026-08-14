"""Legacy in-process pricing compatibility helpers.

Used by CostTracker as the *fallback* pricing path: when a turn carries a
provider-reported ``total_cost_usd`` that value wins (it is authoritative);
only token-derived turns (disk-reader / codex / tmux-hosted, which have no
provider cost) are priced here.

Runtime accounting resolves the versioned catalog in ``cost/catalog.py``.
This table remains for external import compatibility and only permits exact,
normalized identifiers. Unknown models are never mapped to a default rate.
"""

from __future__ import annotations

from dataclasses import dataclass


# Bump whenever FALLBACK_RATES values change so a startup seeder (if added)
# knows to re-upsert cached pricing records.
FALLBACK_VERSION = "2026-06-17"


@dataclass(frozen=True)
class ModelRate:
    """Per-model pricing in USD per 1M tokens."""

    input: float
    output: float
    cache_creation: float = 0.0
    cache_read: float = 0.0


# Dated fallback, USD per 1M tokens. Values current as of 2026-06.
# Cache rates: Claude convention — creation ≈ 1.25× input, read ≈ 0.1× input.
FALLBACK_RATES: dict[str, ModelRate] = {
    "default": ModelRate(input=3.0, output=15.0, cache_creation=3.75, cache_read=0.30),
    "claude-sonnet-4-6": ModelRate(3.0, 15.0, 3.75, 0.30),
    "claude-opus-4-6": ModelRate(5.0, 25.0, 6.25, 0.50),
    "claude-opus-4-7": ModelRate(5.0, 25.0, 6.25, 0.50),
    "claude-opus-4-8": ModelRate(5.0, 25.0, 6.25, 0.50),
    "claude-fable-5": ModelRate(10.0, 50.0, 12.5, 1.0),
    "claude-haiku-4-5": ModelRate(0.25, 1.25, 0.30, 0.03),
    # Coarse family aliases so a bare "opus" / "sonnet" / "haiku" still lands
    # on a sane rate (legacy CostTracker rate-bucket compatibility).
    "opus": ModelRate(5.0, 25.0, 6.25, 0.50),
    "sonnet": ModelRate(3.0, 15.0, 3.75, 0.30),
    "haiku": ModelRate(0.25, 1.25, 0.30, 0.03),
}


def _normalize(model: str) -> str:
    """Dots to dashes so dotted ids (claude-opus-4.8) match dashed keys."""
    return model.replace(".", "-")


def _canonical(s: str) -> str:
    """Strip provider prefix and normalize case/dotted version separators."""
    if "/" in s:
        s = s.rsplit("/", 1)[1]
    return _normalize(s).lower()


def resolve_rate(
    model: str | None,
    rates: dict[str, ModelRate] | None = None,
) -> ModelRate | None:
    """Resolve a model id to its rate.

    Exact matching tolerates case, dotted version separators, and an explicit
    provider prefix. Missing/unknown models return ``None``.
    """
    table = rates or FALLBACK_RATES
    if not model:
        return None
    target = _canonical(model)
    for key, rate in table.items():
        if _canonical(key) == target:
            return rate
    return None
