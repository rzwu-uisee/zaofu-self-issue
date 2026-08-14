from __future__ import annotations

from zf.runtime.workflow_budget_guard import (
    _exceeded_dimensions,
    _measurement,
)


def _meter(*, tokens: int, usd: float, unpriced: int) -> dict:
    return {
        "meter_available": True,
        "entries": 1,
        "total_tokens": tokens,
        "total_usd": usd,
        "unpriced_entries": unpriced,
    }


def test_fail_closed_usd_budget_blocks_unpriced_usage():
    measurement = _measurement(
        baseline=_meter(tokens=0, usd=0, unpriced=0),
        current=_meter(tokens=100, usd=0, unpriced=1),
        elapsed_seconds=1,
        cost_fail_closed=True,
    )

    assert _exceeded_dimensions(
        {"cost_budget_usd": 10, "token_budget": 0},
        measurement,
    ) == ["pricing_unavailable"]


def test_token_only_budget_still_uses_tokens_with_unpriced_usage():
    measurement = _measurement(
        baseline=_meter(tokens=0, usd=0, unpriced=0),
        current=_meter(tokens=50, usd=0, unpriced=1),
        elapsed_seconds=1,
        cost_fail_closed=True,
    )

    assert _exceeded_dimensions(
        {"cost_budget_usd": 0, "token_budget": 100},
        measurement,
    ) == []
    assert _exceeded_dimensions(
        {"cost_budget_usd": 0, "token_budget": 50},
        measurement,
    ) == ["tokens"]
