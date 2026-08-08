"""TaskAttempt identity section materialization for worker briefings."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from zf.core.state.atomic_io import atomic_write_text
from zf.runtime.briefing_metrics import refresh_briefing_metrics
from zf.runtime.task_pipeline_attempt_recovery import task_attempt_identity


_BRIEFING_MARKER = "<!-- ZF:TASK-ATTEMPT -->"


def bind_task_attempt_to_briefing(
    path: Path,
    attempt: Mapping[str, Any],
) -> None:
    try:
        body = path.read_text(encoding="utf-8")
    except OSError:
        return
    if _BRIEFING_MARKER in body:
        return
    identity = task_attempt_identity(attempt)
    section = (
        "\n\n"
        f"{_BRIEFING_MARKER}\n"
        "## Runtime TaskAttempt Identity\n\n"
        "Kernel-owned attempt; preserve these values. `zf emit --task ...` "
        "auto-fills them from canonical state.\n\n"
        f"- workflow_run_id: `{identity['workflow_run_id']}`\n"
        f"- operation_id: `{identity['operation_id']}`\n"
        f"- attempt_id: `{identity['attempt_id']}`\n"
        f"- lease_id: `{identity['lease_id']}`\n"
        f"- dispatch_id: `{identity['dispatch_id']}`\n"
    )
    atomic_write_text(path, body.rstrip() + section)
    refresh_briefing_metrics(path)


__all__ = ["bind_task_attempt_to_briefing"]
