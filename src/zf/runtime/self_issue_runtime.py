"""Runtime-owned scheduling for Self-Issue semantic assessment."""

from __future__ import annotations

import os
import threading
from typing import Any

from zf.runtime.self_issue_assessment_executor import run_self_issue_assessment
from zf.runtime.self_issue_liveness import self_issue_runtime_status
from zf.runtime.self_issue_service import SelfIssueService
from zf.web.headless_agent import canonical_headless_backend


def schedule_pending_self_issue_assessment(orchestrator: Any) -> bool:
    """Claim and schedule at most one pending assessment in this runtime tick."""

    if self_issue_runtime_status(orchestrator.state_dir) != "live":
        return False
    service = SelfIssueService(
        orchestrator.state_dir,
        orchestrator.event_writer,
        project_root=orchestrator.project_root,
        policy=orchestrator.config.self_issue,
    )
    backend = _orchestrator_backend(orchestrator.config)
    if not backend:
        service.fail_pending_assessment(
            reason="configured orchestrator role has no supported assessment backend",
        )
        return False
    claim = service.claim_pending_assessment(owner_pid=os.getpid())
    if not claim:
        return False

    def _run() -> None:
        run_self_issue_assessment(
            state_dir=orchestrator.state_dir,
            writer=orchestrator.event_writer,
            config=orchestrator.config,
            project_root=orchestrator.project_root,
            start_result=claim,
            backend=backend,
            surface="runtime",
        )

    threading.Thread(
        target=_run,
        name=f"zf-self-issue-assessment-{claim['run_id']}",
        daemon=True,
    ).start()
    return True


def _orchestrator_backend(config: Any) -> str:
    role = next(
        (item for item in getattr(config, "roles", []) if item.name == "orchestrator"),
        None,
    )
    configured = str(
        getattr(role, "backend", "")
        or getattr(getattr(config, "orchestrator", None), "backend", "")
    )
    return canonical_headless_backend(configured)


__all__ = ["schedule_pending_self_issue_assessment", "self_issue_runtime_status"]
