"""Provider-neutral per-turn accounting without discarding raw usage."""

from __future__ import annotations

from typing import Any


def normalize_provider_usage(
    usage: dict[str, Any] | None,
    *,
    backend: str = "",
) -> dict[str, Any]:
    raw = usage if isinstance(usage, dict) else {}
    token_usage = raw.get("tokenUsage")
    if not isinstance(token_usage, dict):
        token_usage = raw.get("token_usage")
    if not isinstance(token_usage, dict):
        token_usage = {}

    cumulative_source = _dict_value(token_usage, "total")
    turn_source = _dict_value(token_usage, "last")
    if not turn_source:
        cumulative_source = _dict_value(raw, "total")
        turn_source = _dict_value(raw, "last")

    if turn_source:
        mode = "provider_cumulative_with_turn_delta"
        turn = _normalize_bucket(turn_source)
        cumulative = _normalize_bucket(cumulative_source)
    else:
        mode = "per_turn"
        turn = _normalize_bucket(raw)
        cumulative = {}
    return {
        "schema_version": "provider.usage_accounting.v1",
        "backend": str(backend or ""),
        "mode": mode,
        "turn": turn,
        "cumulative": cumulative,
        "budget_usage": dict(turn),
    }


def sum_turn_usage(
    runs: list[dict[str, Any]],
) -> dict[str, int]:
    totals: dict[str, int] = {}
    for run in runs:
        accounting = (
            run.get("usage_accounting")
            if isinstance(run.get("usage_accounting"), dict)
            else {}
        )
        turn = (
            accounting.get("turn")
            if isinstance(accounting.get("turn"), dict)
            else {}
        )
        for key, value in turn.items():
            if isinstance(value, bool):
                continue
            try:
                parsed = int(value or 0)
            except (TypeError, ValueError):
                continue
            totals[key] = totals.get(key, 0) + max(parsed, 0)
    return totals


def _normalize_bucket(value: dict[str, Any]) -> dict[str, int]:
    aliases = {
        "input_tokens": (
            "input_tokens",
            "inputTokens",
            "input",
        ),
        "output_tokens": (
            "output_tokens",
            "outputTokens",
            "output",
        ),
        "cached_input_tokens": (
            "cached_input_tokens",
            "cachedInputTokens",
            "cache_read_input_tokens",
            "cacheReadInputTokens",
        ),
        "cache_creation_input_tokens": (
            "cache_creation_input_tokens",
            "cacheCreationInputTokens",
        ),
        "reasoning_tokens": (
            "reasoning_tokens",
            "reasoningTokens",
        ),
        "total_tokens": (
            "total_tokens",
            "totalTokens",
        ),
    }
    normalized: dict[str, int] = {}
    for target, names in aliases.items():
        for name in names:
            if name not in value:
                continue
            try:
                normalized[target] = max(int(value.get(name) or 0), 0)
            except (TypeError, ValueError):
                normalized[target] = 0
            break
    if "total_tokens" not in normalized:
        total = (
            normalized.get("input_tokens", 0)
            + normalized.get("output_tokens", 0)
        )
        if total:
            normalized["total_tokens"] = total
    return normalized


def _dict_value(value: dict[str, Any], key: str) -> dict[str, Any]:
    item = value.get(key)
    return item if isinstance(item, dict) else {}


__all__ = ["normalize_provider_usage", "sum_turn_usage"]
