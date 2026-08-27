"""Bounded, disclosure-safe log evidence extraction for Self-Issue."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from zf.core.self_issue.safe_export import safe_report_text


TAIL_BYTES = 4096
MAX_LOG_REFS = 100
MAX_TAIL_EXCERPTS = 10
MAX_CANDIDATES = 100
MAX_SCAN_FILE_BYTES = 20 * 1024 * 1024
MAX_SCAN_TOTAL_BYTES = 64 * 1024 * 1024
MAX_LINE_CHARS = 4000
MAX_FINDINGS = 20

LOG_FINDING_RELATIONS = frozenset({"supports", "contradicts", "context", "uncertain"})
LOG_FINDING_CONFIDENCES = frozenset({"low", "medium", "high"})
LOG_FINDING_FIELDS = frozenset({"candidate_id", "relation", "confidence", "reason"})

_ANOMALY_PATTERNS = (
    ("exception", re.compile(r"\b(?:exception|traceback|panic|fatal)\b", re.IGNORECASE)),
    ("timeout", re.compile(r"\b(?:timeout|timed\s+out)\b", re.IGNORECASE)),
    ("failure", re.compile(r"\b(?:failed|failure|crash(?:ed)?)\b", re.IGNORECASE)),
    ("rejected", re.compile(r"\b(?:reject(?:ed|ion)?|denied)\b", re.IGNORECASE)),
    ("http_5xx", re.compile(r"(?:\bHTTP\s*)?\b5[0-9]{2}\b", re.IGNORECASE)),
    ("slow", re.compile(r"\b(?:slow|stalled?|blocked|pending)\b", re.IGNORECASE)),
    ("error", re.compile(r"\berror\b|错误|异常", re.IGNORECASE)),
    ("warning", re.compile(r"\bwarn(?:ing)?\b|警告|超时|失败", re.IGNORECASE)),
)


def collect_log_evidence(state_dir: Path) -> dict[str, Any]:
    """Collect log tails and anomaly candidates without deciding relevance."""
    root = Path(state_dir).resolve()
    paths = _log_paths(root)
    refs: list[dict[str, Any]] = []
    excerpts: list[dict[str, Any]] = []
    candidates: list[dict[str, Any]] = []
    scanned_bytes = 0
    scanned_files = 0
    skipped_oversized = 0
    skipped_budget = 0

    for path in paths:
        try:
            size = path.stat().st_size
            relative = path.relative_to(root).as_posix()
        except (OSError, ValueError):
            continue
        if len(refs) < MAX_LOG_REFS:
            refs.append({"path": relative, "size": size})
        if len(excerpts) < MAX_TAIL_EXCERPTS:
            tail = _read_tail(path, size)
            if tail:
                excerpts.append({"path": relative, "redacted_tail": tail})
        if size > MAX_SCAN_FILE_BYTES:
            skipped_oversized += 1
            continue
        if scanned_bytes + size > MAX_SCAN_TOTAL_BYTES:
            skipped_budget += 1
            continue
        scanned_bytes += size
        scanned_files += 1
        if len(candidates) >= MAX_CANDIDATES:
            continue
        candidates.extend(
            _anomaly_candidates(path, relative, limit=MAX_CANDIDATES - len(candidates)),
        )

    return {
        "log_refs": refs,
        "log_excerpts": excerpts,
        "log_error_candidates": candidates,
        "log_scan": {
            "scanned_files": scanned_files,
            "scanned_bytes": scanned_bytes,
            "candidate_count": len(candidates),
            "skipped_oversized_files": skipped_oversized,
            "skipped_budget_files": skipped_budget,
            "bounded": True,
        },
    }


def normalize_log_findings(
    value: object,
    *,
    allowed_candidate_ids: set[str] | None = None,
) -> list[dict[str, str]]:
    """Validate semantic findings and bind them to Kernel-issued candidates."""
    if not isinstance(value, list) or len(value) > MAX_FINDINGS:
        raise ValueError("assessment log_findings must be a bounded list")
    normalized: list[dict[str, str]] = []
    seen: set[str] = set()
    for raw in value:
        if not isinstance(raw, dict) or set(raw) != LOG_FINDING_FIELDS:
            raise ValueError("assessment log finding does not match the canonical schema")
        candidate_id = str(raw.get("candidate_id") or "")
        relation = str(raw.get("relation") or "")
        confidence = str(raw.get("confidence") or "")
        reason = safe_report_text(str(raw.get("reason") or "")).strip()[:500]
        if (
            not candidate_id
            or candidate_id in seen
            or relation not in LOG_FINDING_RELATIONS
            or confidence not in LOG_FINDING_CONFIDENCES
            or not reason
        ):
            raise ValueError("assessment log finding is invalid")
        if allowed_candidate_ids is not None and candidate_id not in allowed_candidate_ids:
            raise ValueError("assessment references an unknown log candidate")
        seen.add(candidate_id)
        normalized.append({
            "candidate_id": candidate_id,
            "relation": relation,
            "confidence": confidence,
            "reason": reason,
        })
    return normalized


def verified_log_candidate_map(value: object) -> dict[str, dict[str, Any]]:
    """Return only candidates whose ID and digest match their bounded content."""
    if not isinstance(value, list):
        return {}
    verified: dict[str, dict[str, Any]] = {}
    for raw in value[:MAX_CANDIDATES]:
        if not isinstance(raw, dict):
            continue
        relative = Path(str(raw.get("path") or ""))
        try:
            line = int(raw.get("line") or 0)
        except (TypeError, ValueError):
            continue
        context = raw.get("context")
        if (
            not relative.as_posix()
            or relative.is_absolute()
            or ".." in relative.parts
            or line < 1
            or not isinstance(context, list)
            or not all(isinstance(item, str) for item in context)
        ):
            continue
        identity = {
            "path": relative.as_posix(),
            "line": line,
            "category": str(raw.get("category") or ""),
            "redacted_line": str(raw.get("redacted_line") or ""),
            "context": context,
        }
        digest = hashlib.sha256(
            json.dumps(identity, ensure_ascii=False, sort_keys=True).encode("utf-8"),
        ).hexdigest()
        candidate_id = str(raw.get("candidate_id") or "")
        if (
            not identity["category"]
            or not identity["redacted_line"]
            or digest != str(raw.get("sha256") or "")
            or candidate_id != f"logc-{digest[:16]}"
        ):
            continue
        verified[candidate_id] = {**identity, "candidate_id": candidate_id, "sha256": digest}
    return verified


def _log_paths(root: Path) -> list[Path]:
    paths: list[Path] = []
    for directory in (root / "logs", root / "diagnostics"):
        if not directory.is_dir():
            continue
        for path in directory.rglob("*"):
            try:
                resolved = path.resolve()
            except OSError:
                continue
            if (
                path.is_symlink()
                or not resolved.is_relative_to(root)
                or not resolved.is_file()
            ):
                continue
            paths.append(resolved)
    return sorted(
        paths,
        key=lambda item: (-_mtime_ns(item), item.relative_to(root).as_posix()),
    )


def _mtime_ns(path: Path) -> int:
    try:
        return path.stat().st_mtime_ns
    except OSError:
        return 0


def _read_tail(path: Path, size: int) -> str:
    try:
        with path.open("rb") as handle:
            handle.seek(max(0, size - TAIL_BYTES))
            value = handle.read(TAIL_BYTES).decode("utf-8", errors="replace")
    except OSError:
        return ""
    return _meaningful_excerpt(safe_report_text(value))


def _anomaly_candidates(path: Path, relative: str, *, limit: int) -> list[dict[str, Any]]:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    if "\x00" in text:
        return []
    lines = text.splitlines()
    candidates: list[dict[str, Any]] = []
    for index, raw in enumerate(lines):
        category = _anomaly_category(raw)
        if not category:
            continue
        redacted_line = safe_report_text(raw[:MAX_LINE_CHARS]).strip()
        if not redacted_line:
            continue
        context = [
            safe_report_text(lines[item][:MAX_LINE_CHARS]).rstrip()
            for item in range(max(0, index - 1), min(len(lines), index + 2))
            if lines[item].strip()
        ]
        identity = {
            "path": relative,
            "line": index + 1,
            "category": category,
            "redacted_line": redacted_line,
            "context": context,
        }
        digest = hashlib.sha256(
            json.dumps(identity, ensure_ascii=False, sort_keys=True).encode("utf-8"),
        ).hexdigest()
        candidates.append({
            "candidate_id": f"logc-{digest[:16]}",
            **identity,
            "sha256": digest,
        })
        if len(candidates) >= limit:
            break
    return candidates


def _anomaly_category(value: str) -> str:
    for category, pattern in _ANOMALY_PATTERNS:
        if pattern.search(value):
            return category
    return ""


def _meaningful_excerpt(value: str) -> str:
    lines = []
    for raw in str(value or "").splitlines():
        stripped = raw.strip()
        if not stripped or stripped in {"[]", "{}", "null", "None"}:
            continue
        lines.append(raw.rstrip())
    return "\n".join(lines[-120:]).strip()
