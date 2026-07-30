"""Small serialization helpers for Workflow Request state."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def read_json(path: Path) -> dict[str, Any]:
    if not str(path) or not path.exists():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def strings(value: object) -> list[str]:
    if isinstance(value, str):
        return [value.strip()] if value.strip() else []
    if not isinstance(value, (list, tuple)):
        return []
    return list(dict.fromkeys(
        str(item).strip()
        for item in value
        if str(item).strip()
    ))


def safe_id(value: str) -> str:
    return (
        "".join(
            character
            if character.isalnum() or character in "-_."
            else "-"
            for character in value
        )
        or "request"
    )


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
