"""Nested fanout payload lookup and ref normalization helpers."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


def payload_ref_value(value: object) -> str:
    if isinstance(value, Mapping):
        return str(value.get("path") or value.get("ref") or "").strip()
    return str(value or "").strip()


def payload_or_report_value(payload: dict, key: str) -> Any:
    value = payload.get(key)
    if value not in (None, ""):
        return value
    report = payload.get("report")
    if isinstance(report, dict):
        report_value = report.get(key)
        if report_value not in (None, ""):
            return report_value
    inner = payload.get("payload")
    if isinstance(inner, dict):
        inner_value = inner.get(key)
        if inner_value not in (None, ""):
            return inner_value
        inner_report = inner.get("report")
        if isinstance(inner_report, dict):
            inner_report_value = inner_report.get(key)
            if inner_report_value not in (None, ""):
                return inner_report_value
    trigger = payload.get("trigger_payload")
    if not isinstance(trigger, dict) and isinstance(inner, dict):
        trigger = inner.get("trigger_payload")
    if isinstance(trigger, dict):
        return trigger.get(key)
    return None


def collect_payload_list(payloads: list[dict], key: str) -> list[str]:
    values: list[str] = []
    for payload in payloads:
        raw = payload_or_report_value(payload, key)
        if isinstance(raw, list):
            for item in raw:
                value = payload_ref_value(item)
                if value:
                    values.append(value)
        elif raw not in (None, ""):
            value = payload_ref_value(raw)
            if value:
                values.append(value)
    return values


def first_child_value(
    manifest: dict,
    payloads: list[dict],
    key: str,
) -> str:
    for payload in payloads:
        value = payload_or_report_value(payload, key)
        if value not in (None, ""):
            return str(value)
    value = manifest.get(key)
    return str(value) if value not in (None, "") else ""


def first_child_mapping(
    manifest: dict,
    payloads: list[dict],
    key: str,
) -> dict:
    for payload in payloads:
        value = payload_or_report_value(payload, key)
        if isinstance(value, dict):
            return dict(value)
    value = manifest.get(key)
    return dict(value) if isinstance(value, dict) else {}


def first_nonempty_plan_ports(payloads: list[dict]) -> list[dict] | None:
    """Prefer producer ports over an earlier verdict-only empty list."""

    saw_empty = False
    for payload in payloads:
        ports = payload_or_report_value(payload, "plan_ports")
        if not isinstance(ports, list):
            synthesis = payload_or_report_value(payload, "plan_synthesis_result")
            ports = (
                synthesis.get("plan_ports")
                if isinstance(synthesis, dict)
                else None
            )
        if not isinstance(ports, list):
            continue
        normalized = [dict(item) for item in ports if isinstance(item, dict)]
        if normalized:
            return normalized
        saw_empty = True
    return [] if saw_empty else None
