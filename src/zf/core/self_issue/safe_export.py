"""Fail-closed text sanitation for externally disclosable Self-Issue fields."""

from __future__ import annotations

import re
from typing import Any

from zf.core.security.redaction import redact_text

_EMAIL_RE = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.IGNORECASE)
_PERSONAL_PATH_RE = re.compile(r"(?<![A-Za-z0-9_])/(?:home|Users)/[^\s,;]+")


def safe_report_text(value: str) -> str:
    redacted = redact_text(value)
    redacted = _EMAIL_RE.sub("[REDACTED_EMAIL]", redacted)
    return _PERSONAL_PATH_RE.sub("[REDACTED_PERSONAL_PATH]", redacted)


def safe_export_obj(value: Any) -> Any:
    if isinstance(value, str):
        return safe_report_text(value)
    if isinstance(value, dict):
        return {str(key): safe_export_obj(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [safe_export_obj(item) for item in value]
    return value
