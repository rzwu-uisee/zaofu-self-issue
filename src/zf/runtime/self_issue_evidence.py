"""Safe, bounded evidence projections for Self-Issue assessment."""

from __future__ import annotations

import json
import math
from collections import defaultdict, deque
from pathlib import Path
from typing import Any


def summarize_web_api_timing(path: Path, *, max_rows: int = 2000) -> dict[str, Any]:
    """Aggregate the timing ledger without exposing project or request identities."""
    if not path.is_file():
        return {}
    rows: deque[str] = deque(maxlen=max_rows)
    try:
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            rows.extend(handle)
    except OSError:
        return {}
    grouped: dict[tuple[str, str, int], list[tuple[float, int]]] = defaultdict(list)
    for raw in rows:
        try:
            item = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if not isinstance(item, dict) or item.get("schema_version") != "web-api-timing.v1":
            continue
        method = str(item.get("method") or "").upper()
        route = str(item.get("route") or "")
        try:
            status = int(item.get("status_code") or 0)
            elapsed = float(item.get("elapsed_ms") or 0.0)
            response_bytes = int(item.get("response_bytes") or 0)
        except (TypeError, ValueError):
            continue
        if (
            method not in {"DELETE", "GET", "HEAD", "OPTIONS", "PATCH", "POST", "PUT"}
            or not route.startswith("/")
            or "?" in route
            or len(route) > 200
            or not 100 <= status <= 599
            or not math.isfinite(elapsed)
            or elapsed < 0
        ):
            continue
        grouped[(method, route, status)].append((elapsed, max(0, response_bytes)))
    summaries = []
    for (method, route, status), values in grouped.items():
        elapsed_values = sorted(value[0] for value in values)
        summaries.append({
            "method": method,
            "route": route,
            "status_code": status,
            "count": len(values),
            "p50_ms": round(_percentile(elapsed_values, 0.50), 3),
            "p95_ms": round(_percentile(elapsed_values, 0.95), 3),
            "max_ms": round(elapsed_values[-1], 3),
            "max_response_bytes": max(value[1] for value in values),
        })
    summaries.sort(key=lambda item: (-item["max_ms"], item["route"], item["method"]))
    return {
        "schema_version": "self-issue-web-timing-summary.v1",
        "sample_count": sum(item["count"] for item in summaries),
        "routes": summaries[:50],
    }


def _percentile(values: list[float], fraction: float) -> float:
    if not values:
        return 0.0
    index = max(0, min(len(values) - 1, math.ceil(len(values) * fraction) - 1))
    return values[index]
