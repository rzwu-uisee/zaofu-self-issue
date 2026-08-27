"""Disclosure-safe activity projection for Self-Issue evidence and assessment."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from zf.core.self_issue.models import utc_now
from zf.core.state.atomic_io import atomic_write_text
from zf.core.state.locks import locked_path


_MAX_ENTRIES = 40


class EvidenceActivityStore:
    def __init__(self, state_dir: Path, *, draft_id: str, run_id: str) -> None:
        self.path = Path(state_dir) / "self-issues" / "evidence-activity" / f"{draft_id}.json"
        self.draft_id = draft_id
        self.run_id = run_id

    def start(self, *, actor: str = "kernel") -> None:
        self.phase(actor, "preparing", "Preparing a bounded read-only evidence run", reset=True)

    def resume(self, *, actor: str = "orchestrator") -> None:
        self.phase(actor, "resuming", "Resuming from the saved evidence checkpoint")

    def phase(self, actor: str, phase: str, label: str, *, reset: bool = False) -> None:
        self._write(status="running", actor=actor, phase=phase, label=label, reset=reset)

    def complete(self, *, actor: str = "kernel") -> None:
        self._write(
            status="completed", actor=actor, phase="completed",
            label="Evidence and Orchestrator assessment completed",
        )

    def fail(self, *, actor: str = "kernel") -> None:
        self._write(
            status="failed", actor=actor, phase="failed",
            label="Evidence assessment stopped with an error",
        )

    def interrupt(self, *, actor: str = "kernel") -> None:
        self._write(
            status="interrupted", actor=actor, phase="interrupted",
            label="Evidence run interrupted; progress was saved locally",
        )

    def limited(self, *, actor: str = "kernel", reason: str) -> None:
        self._write(
            status="completed", actor=actor, phase="limited",
            label=f"Limited report selected: {str(reason)[:140]}",
        )

    def _write(
        self, *, status: str, actor: str, phase: str, label: str, reset: bool = False,
    ) -> None:
        with locked_path(self.path):
            current = _read_json(self.path)
            entries = [] if reset or current.get("draft_id") != self.draft_id else list(
                current.get("entries") or [],
            )
            entry = {
                "actor": str(actor or "kernel")[:60],
                "phase": str(phase)[:60],
                "label": str(label)[:180],
                "at": utc_now(),
            }
            if not entries or any(entries[-1].get(key) != entry[key] for key in ("actor", "phase")):
                entries.append(entry)
            body = {
                "schema_version": "self-issue-evidence-activity.v1",
                "draft_id": self.draft_id,
                "run_id": self.run_id,
                "status": status,
                "phase": phase,
                "updated_at": utc_now(),
                "entries": entries[-_MAX_ENTRIES:],
            }
            atomic_write_text(
                self.path,
                json.dumps(body, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            )


def read_evidence_activity(state_dir: Path, draft_id: str) -> dict[str, Any] | None:
    path = Path(state_dir) / "self-issues" / "evidence-activity" / f"{draft_id}.json"
    value = _read_json(path)
    if (
        value.get("schema_version") != "self-issue-evidence-activity.v1"
        or value.get("draft_id") != draft_id
        or value.get("status") not in {"running", "interrupted", "completed", "failed"}
        or not isinstance(value.get("entries"), list)
    ):
        return None
    entries = []
    for raw in value["entries"][-_MAX_ENTRIES:]:
        if not isinstance(raw, dict):
            return None
        actor = str(raw.get("actor") or "")[:60]
        phase = str(raw.get("phase") or "")[:60]
        label = str(raw.get("label") or "")[:180]
        at = str(raw.get("at") or "")[:40]
        if not actor or not phase or not label:
            return None
        entries.append({"actor": actor, "phase": phase, "label": label, "at": at})
    return {
        "schema_version": "self-issue-evidence-activity.v1",
        "status": str(value["status"]),
        "run_id": str(value.get("run_id") or ""),
        "phase": str(value.get("phase") or "")[:60],
        "updated_at": str(value.get("updated_at") or "")[:40],
        "entries": entries,
    }


def _read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}
