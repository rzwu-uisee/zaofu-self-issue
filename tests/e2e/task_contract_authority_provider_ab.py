#!/usr/bin/env python3
"""Real Codex A/B drill for stale Task contract replay and result admission.

Run this file twice with ``PYTHONPATH`` pointed at two ZaoFu revisions. The
driver itself and the Provider prompt stay identical; only the imported
runtime changes. It intentionally uses APIs available in the frozen baseline.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
from collections import Counter
from dataclasses import asdict
from pathlib import Path
from typing import Any, Mapping

from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.core.events.writer import EventWriter
from zf.core.task.schema import Task, TaskContract
from zf.core.task.store import TaskStore
from zf.runtime.call_result_admission import CallResultAdmissionService
from zf.runtime.housekeeping import apply_task_contract_event
from zf.runtime.impl_self_check import completion_payload_template
from zf.runtime.run_manager import run_goal_completion_gate_event
from zf.runtime.task_contract_snapshot import (
    build_task_contract_snapshot,
    write_task_contract_snapshot,
)
from zf.runtime.workflow_operation import WorkflowOperationService
from zf.runtime.workflow_start_inputs import ensure_workflow_managed_task


TASK_ID = "TASK-CONTRACT-AB"
RUN_ID = "run-contract-authority-ab"
OPERATION_ID = "wop-contract-authority-ab-r1"
PROMPT = """Implement this exact small task in the current git repository.

Create `src/greeting.py` with `greet(name: str) -> str`. Strip surrounding
whitespace from `name`; return `Hello, <name>!`; raise ValueError when the
stripped name is empty. Create `tests/test_greeting.py` using only unittest,
covering a normal name, whitespace trimming, and the empty-name error.

Run `python -m unittest discover -s tests -p 'test_*.py' -v`. Do not edit any
other files. Do not commit. In the final response, state the command result.
"""


class ProviderDrillError(RuntimeError):
    """The real Provider A/B drill could not produce trustworthy evidence."""


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--variant", required=True, choices=("A", "B"))
    parser.add_argument("--project-root", required=True, type=Path)
    parser.add_argument("--report", required=True, type=Path)
    parser.add_argument("--timeout-seconds", type=int, default=600)
    parser.add_argument("--model", default=os.environ.get("ZF_AB_CODEX_MODEL", ""))
    parser.add_argument(
        "--reasoning-effort",
        default=os.environ.get("ZF_AB_CODEX_REASONING_EFFORT", "medium"),
    )
    parser.add_argument("--confirm-real", action="store_true")
    return parser


def _run(
    command: list[str],
    *,
    cwd: Path,
    timeout: int = 120,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        command,
        cwd=cwd,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        env={
            **os.environ,
            "GIT_AUTHOR_DATE": "2026-08-13T00:00:00Z",
            "GIT_COMMITTER_DATE": "2026-08-13T00:00:00Z",
        },
        timeout=timeout,
        check=False,
    )
    if check and result.returncode != 0:
        raise ProviderDrillError(
            f"command failed ({result.returncode}): {' '.join(command)}\n"
            f"stdout:\n{result.stdout[-4000:]}\n"
            f"stderr:\n{result.stderr[-4000:]}"
        )
    return result


def _write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(dict(value), ensure_ascii=False, indent=2, sort_keys=True)
        + "\n",
        encoding="utf-8",
    )


def _initial_contract() -> TaskContract:
    return TaskContract(
        behavior="R1: implement greeting behavior",
        verification="python -m unittest discover -s tests -p 'test_*.py' -v",
        validation={
            "commands": [{
                "id": "unit-greeting",
                "command": "python -m unittest discover -s tests -p 'test_*.py' -v",
                "owner": "impl_self_check",
                "tier": "task_non_smoke",
                "deterministic": True,
                "reusable": True,
                "timeout_seconds": 120,
            }],
        },
        scope=["src/greeting.py", "tests/test_greeting.py"],
        acceptance="Greeting implementation and tests pass.",
        acceptance_criteria=[{
            "id": "AC-R1",
            "text": "Greeting implementation and tests pass.",
            "verification_owner": "impl_self_check",
            "verification_tier": "task_non_smoke",
            "verification_command_ids": ["unit-greeting"],
            "producer_paths": ["src/greeting.py", "tests/test_greeting.py"],
        }],
        evidence_contract={
            "task_map_generation": "G1",
            "source_refs": {"task_map_generation": "G1"},
        },
    )


def _takeover_contract() -> TaskContract:
    contract = _initial_contract()
    contract.behavior = "R2: workflow takeover owns greeting delivery"
    contract.acceptance_criteria = [
        *contract.acceptance_criteria,
        {
            "id": "AC-R2-SENTINEL",
            "text": "Current R2 workflow ownership remains canonical.",
            "verification_owner": "task_verify",
            "verification_tier": "task_non_smoke",
            "verification_command_ids": [],
        },
    ]
    contract.evidence_contract = {
        "task_map_generation": "G2",
        "source_refs": {"task_map_generation": "G2"},
    }
    return contract


def _init_project(root: Path) -> str:
    root.mkdir(parents=True, exist_ok=False)
    _run(["git", "init", "-q", "-b", "main"], cwd=root)
    _run(["git", "config", "user.name", "ZaoFu Provider A/B"], cwd=root)
    _run(
        ["git", "config", "user.email", "zaofu-provider-ab@example.invalid"],
        cwd=root,
    )
    (root / ".gitignore").write_text(
        ".zf-ab/\n__pycache__/\n",
        encoding="utf-8",
    )
    (root / "README.md").write_text(
        "# Task Contract Authority Provider A/B\n",
        encoding="utf-8",
    )
    _run(["git", "add", ".gitignore", "README.md"], cwd=root)
    _run(["git", "commit", "-q", "-m", "chore: initialize A/B fixture"], cwd=root)
    return _run(["git", "rev-parse", "HEAD"], cwd=root).stdout.strip()


def _provider_command(
    *,
    root: Path,
    output_path: Path,
    model: str,
    reasoning_effort: str,
) -> list[str]:
    return [
        "codex",
        "exec",
        "--json",
        "--ephemeral",
        "--ignore-user-config",
        "--dangerously-bypass-approvals-and-sandbox",
        *( ["--model", model] if model else [] ),
        *( ["--config", f'model_reasoning_effort="{reasoning_effort}"']
           if reasoning_effort else [] ),
        "--output-last-message",
        str(output_path),
        "-C",
        str(root),
        PROMPT,
    ]


def _provider_audit(stdout: str) -> dict[str, Any]:
    rows = [json.loads(line) for line in stdout.splitlines() if line.strip()]
    thread_id = ""
    commands: list[str] = []
    file_changes: list[dict[str, Any]] = []
    usage: dict[str, Any] = {}
    for row in rows:
        if row.get("type") == "thread.started":
            thread_id = str(row.get("thread_id") or "")
        item = row.get("item") if isinstance(row.get("item"), dict) else {}
        if item.get("type") == "command_execution":
            commands.append(str(item.get("command") or ""))
        elif item.get("type") in {"file_change", "file_write"}:
            file_changes.append(dict(item))
        if isinstance(row.get("usage"), dict):
            usage = dict(row["usage"])
    return {
        "provider_session_id": thread_id,
        "commands": commands,
        "file_changes": file_changes,
        "usage": usage,
        "result_rows": len(rows),
    }


def _result_payload(
    *,
    snapshot: Mapping[str, Any],
    descriptor: Mapping[str, Any],
    target_commit: str,
) -> dict[str, Any]:
    template = completion_payload_template(
        contract_snapshot=snapshot,
        task_item={
            "attempt_id": "attempt-r1",
            "contract_snapshot_ref": descriptor["ref"],
            "contract_snapshot_digest": descriptor["sha256"],
        },
        task_id=TASK_ID,
        run_id="attempt-r1",
        child_id="child-r1",
    )
    self_check = dict(template["impl_self_check"])
    self_check["source_commit"] = target_commit
    self_check["target_commit"] = target_commit
    for receipt in self_check["command_receipts"]:
        receipt["target_commit"] = target_commit
        receipt["evidence_refs"] = ["provider-ab://unit-test-output"]
    for result in self_check["acceptance_results"]:
        result["evidence_refs"] = ["provider-ab://acceptance"]
    self_check["evidence_refs"] = ["provider-ab://implementation"]
    identity = {
        key: value
        for key, value in snapshot.items()
        if key in {
            "workflow_run_id",
            "task_id",
            "contract_authority_revision",
            "execution_owner",
            "workflow_request_id",
            "workflow_request_revision",
            "origin_binding_digest",
            "contract_revision",
            "task_map_generation",
            "base_commit",
            "task_ref",
        }
    }
    return {
        **identity,
        "contract_snapshot_ref": descriptor["ref"],
        "contract_snapshot_digest": descriptor["sha256"],
        "attempt_id": "attempt-r1",
        "target_commit": target_commit,
        "execution_status": "completed",
        "verdict": "passed",
        "changed_files": ["src/greeting.py", "tests/test_greeting.py"],
        "evidence_refs": ["provider-ab://implementation"],
        "known_gaps": [],
        "summary": "Real Codex implemented and tested the R1 task.",
        "self_check": self_check,
    }


def run(args: argparse.Namespace) -> dict[str, Any]:
    if not args.confirm_real:
        raise ProviderDrillError("real Provider execution requires --confirm-real")
    root = args.project_root.resolve()
    base_commit = _init_project(root)
    state_dir = root / ".zf-ab"
    state_dir.mkdir()
    event_log = EventLog(state_dir / "events.jsonl")
    writer = EventWriter(event_log)
    store = TaskStore(state_dir / "kanban.json")
    r1 = _initial_contract()
    task = Task(id=TASK_ID, title="Implement greeting", contract=r1)
    store.add(task)
    creation_audit = ZfEvent(
        type="task.contract.update",
        actor="zf-cli",
        task_id=TASK_ID,
        correlation_id=RUN_ID,
        payload={
            "source": "task.create-from-contract",
            "contract": asdict(r1),
            "contract_digest": "r1-creation-audit",
        },
    )
    writer.append(creation_audit)
    writer.append(ZfEvent(
        type="run.goal.started",
        actor="orchestrator",
        correlation_id=RUN_ID,
        payload={"run_id": RUN_ID, "objective": "implement greeting"},
    ))
    snapshot = build_task_contract_snapshot(
        task,
        workflow_run_id=RUN_ID,
        task_map_generation_id="G1",
        base_commit=base_commit,
        task_ref="task/TASK-CONTRACT-AB",
    )
    snapshot_descriptor = write_task_contract_snapshot(
        state_dir,
        snapshot,
        source_event_id=creation_audit.id,
    )

    operation_service = WorkflowOperationService(
        state_dir=state_dir,
        event_log=event_log,
        event_writer=writer,
    )
    operation_request = {
        "backend": "codex",
        "prompt_sha256": hashlib.sha256(PROMPT.encode("utf-8")).hexdigest(),
        "result_identity": {
            **{
                key: snapshot[key]
                for key in (
                    "workflow_run_id",
                    "task_id",
                    "contract_revision",
                    "task_map_generation",
                    "base_commit",
                    "task_ref",
                )
            },
            "contract_snapshot_ref": snapshot_descriptor["ref"],
            "contract_snapshot_digest": snapshot_descriptor["sha256"],
        },
    }
    ensured = operation_service.ensure_operation(
        workflow_run_id=RUN_ID,
        operation_id=OPERATION_ID,
        operation_type="fanout_writer_child",
        request=operation_request,
        parent_stage_id="impl",
        task_id=TASK_ID,
        role_instance="dev-r1",
        correlation_id=RUN_ID,
    )
    operation_service.mark_started(
        operation_id=OPERATION_ID,
        request_hash=ensured.request_hash,
        workflow_run_id=RUN_ID,
        task_id=TASK_ID,
        role_instance="dev-r1",
        provider_session_id="pending-real-codex",
        correlation_id=RUN_ID,
    )

    output_path = state_dir / "provider-last-message.txt"
    command = _provider_command(
        root=root,
        output_path=output_path,
        model=args.model,
        reasoning_effort=args.reasoning_effort,
    )
    provider_started = time.monotonic()
    provider = subprocess.Popen(
        command,
        cwd=root,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )

    current_before_takeover = store.get(TASK_ID)
    assert current_before_takeover is not None
    current_before_takeover.contract = _takeover_contract()
    ensure_workflow_managed_task(
        state_dir=state_dir,
        workflow_task=current_before_takeover,
        writer=writer,
        actor="orchestrator",
        causation_id=creation_audit.id,
        correlation_id=RUN_ID,
    )
    after_takeover = store.get(TASK_ID)
    assert after_takeover is not None
    apply_task_contract_event(store, creation_audit)
    after_replay = store.get(TASK_ID)
    assert after_replay is not None

    try:
        stdout, stderr = provider.communicate(timeout=args.timeout_seconds)
    except subprocess.TimeoutExpired as exc:
        provider.kill()
        provider.communicate()
        raise ProviderDrillError(
            f"Codex exceeded {args.timeout_seconds}s"
        ) from exc
    provider_duration = round(time.monotonic() - provider_started, 3)
    if provider.returncode != 0:
        raise ProviderDrillError(
            f"Codex exited {provider.returncode}\n"
            f"stdout:\n{stdout[-4000:]}\nstderr:\n{stderr[-4000:]}"
        )
    provider_audit = _provider_audit(stdout)
    provider_session_id = str(provider_audit["provider_session_id"] or "")
    test_result = _run(
        [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-p", "test_*.py", "-v"],
        cwd=root,
        timeout=120,
    )
    changed_files = _run(
        ["git", "status", "--short", "--untracked-files=all"], cwd=root
    ).stdout.splitlines()
    unexpected = [
        line for line in changed_files
        if line[3:] not in {"src/greeting.py", "tests/test_greeting.py"}
    ]
    if unexpected:
        raise ProviderDrillError(f"Provider changed unexpected files: {unexpected}")
    _run(["git", "add", "src/greeting.py", "tests/test_greeting.py"], cwd=root)
    _run(["git", "commit", "-q", "-m", "feat: implement greeting"], cwd=root)
    target_commit = _run(["git", "rev-parse", "HEAD"], cwd=root).stdout.strip()

    operation = {
        "workflow_run_id": RUN_ID,
        "operation_id": OPERATION_ID,
        "request_hash": ensured.request_hash,
        "operation_type": "fanout_writer_child",
        "output_profile_id": "implementation",
        "output_profile_revision": "1",
        "result_identity": operation_request["result_identity"],
    }
    implementation = _result_payload(
        snapshot=snapshot,
        descriptor=snapshot_descriptor,
        target_commit=target_commit,
    )
    result_event = ZfEvent(
        type="dev.build.done",
        actor="dev-r1",
        task_id=TASK_ID,
        correlation_id=RUN_ID,
        payload={
            "operation_id": OPERATION_ID,
            "request_hash": ensured.request_hash,
            "workflow_run_id": RUN_ID,
            "provider_session_id": provider_session_id,
            **implementation,
            "implementation_result": implementation,
        },
    )
    writer.append(result_event)
    admission = CallResultAdmissionService(
        state_dir=state_dir,
        event_log=event_log,
        event_writer=writer,
        operation_service=operation_service,
    ).report_legacy_result(
        result_event,
        mode="blocking",
        operation=operation,
        require_semantic_submit=True,
        semantic_submit=True,
    )

    current_task = store.get(TASK_ID)
    assert current_task is not None
    current_generation = str(
        current_task.contract.evidence_contract.get("task_map_generation")
        or "G1"
    )
    current_snapshot = build_task_contract_snapshot(
        current_task,
        workflow_run_id=RUN_ID,
        task_map_generation_id=current_generation,
        base_commit=base_commit,
        task_ref="task/TASK-CONTRACT-AB",
    )
    current_descriptor = write_task_contract_snapshot(
        state_dir,
        current_snapshot,
        source_event_id=result_event.id,
    )
    current_operation_id = "wop-contract-authority-ab-current"
    current_request = {
        "backend": "codex",
        "prompt_sha256": hashlib.sha256(PROMPT.encode("utf-8")).hexdigest(),
        "result_identity": {
            **{
                key: current_snapshot[key]
                for key in (
                    "workflow_run_id",
                    "task_id",
                    "contract_revision",
                    "task_map_generation",
                    "base_commit",
                    "task_ref",
                )
            },
            **{
                key: current_snapshot[key]
                for key in (
                    "contract_authority_revision",
                    "execution_owner",
                    "workflow_request_id",
                    "workflow_request_revision",
                    "origin_binding_digest",
                )
                if current_snapshot.get(key) not in (None, "", 0)
            },
            "contract_snapshot_ref": current_descriptor["ref"],
            "contract_snapshot_digest": current_descriptor["sha256"],
        },
    }
    current_ensured = operation_service.ensure_operation(
        workflow_run_id=RUN_ID,
        operation_id=current_operation_id,
        operation_type="fanout_writer_child",
        request=current_request,
        parent_stage_id="impl",
        task_id=TASK_ID,
        role_instance="dev-current",
        correlation_id=RUN_ID,
    )
    operation_service.mark_started(
        operation_id=current_operation_id,
        request_hash=current_ensured.request_hash,
        workflow_run_id=RUN_ID,
        task_id=TASK_ID,
        role_instance="dev-current",
        provider_session_id=provider_session_id,
        correlation_id=RUN_ID,
    )
    current_implementation = _result_payload(
        snapshot=current_snapshot,
        descriptor=current_descriptor,
        target_commit=target_commit,
    )
    current_result_event = ZfEvent(
        type="dev.build.done",
        actor="dev-current",
        task_id=TASK_ID,
        correlation_id=RUN_ID,
        payload={
            "operation_id": current_operation_id,
            "request_hash": current_ensured.request_hash,
            "workflow_run_id": RUN_ID,
            "provider_session_id": provider_session_id,
            **current_implementation,
            "implementation_result": current_implementation,
        },
    )
    writer.append(current_result_event)
    current_admission = CallResultAdmissionService(
        state_dir=state_dir,
        event_log=event_log,
        event_writer=writer,
        operation_service=operation_service,
    ).report_legacy_result(
        current_result_event,
        mode="blocking",
        operation={
            "workflow_run_id": RUN_ID,
            "operation_id": current_operation_id,
            "request_hash": current_ensured.request_hash,
            "operation_type": "fanout_writer_child",
            "output_profile_id": "implementation",
            "output_profile_revision": "1",
            "result_identity": current_request["result_identity"],
        },
        require_semantic_submit=True,
        semantic_submit=True,
    )

    claim = writer.append(ZfEvent(
        type="run.goal.completion.claimed",
        actor="orchestrator",
        correlation_id=RUN_ID,
        payload={
            "run_id": RUN_ID,
            "claim_id": f"claim-{args.variant}",
            "objective": "implement greeting",
            "target_commit": target_commit,
            "task_map_generation": current_generation,
        },
    ))
    terminal = run_goal_completion_gate_event(
        event_log.read_all(),
        claim=claim,
        required_operation_ids=[current_operation_id],
    )
    if terminal is not None:
        writer.append(terminal)

    events = event_log.read_all()
    counts = Counter(event.type for event in events)
    final_task = store.get(TASK_ID)
    assert final_task is not None
    evidence = final_task.contract.evidence_contract
    first_class_binding = getattr(final_task, "execution_binding", None)
    report = {
        "schema_version": "task-contract-authority-provider-ab.v1",
        "variant": args.variant,
        "runtime_source": str(Path(__file__).resolve()),
        "runtime_import": str(Path(sys.modules[TaskStore.__module__].__file__).resolve()),
        "prompt_sha256": hashlib.sha256(PROMPT.encode("utf-8")).hexdigest(),
        "base_commit": base_commit,
        "target_commit": target_commit,
        "provider": {
            **provider_audit,
            "duration_seconds": provider_duration,
            "returncode": provider.returncode,
            "last_message": output_path.read_text(encoding="utf-8") if output_path.exists() else "",
        },
        "verification": {
            "command": "python -m unittest discover -s tests -p 'test_*.py' -v",
            "passed": test_result.returncode == 0,
            "stdout": test_result.stdout,
            "stderr": test_result.stderr,
        },
        "takeover": {
            "after_takeover_behavior": after_takeover.contract.behavior,
            "after_replay_behavior": after_replay.contract.behavior,
            "after_replay_execution_owner": str(
                getattr(first_class_binding, "owner", "")
                or evidence.get("execution_owner")
                or ""
            ),
            "after_replay_authority_revision": str(
                getattr(final_task, "contract_authority_revision", "") or ""
            ),
            "after_replay_authority_sequence": int(
                getattr(final_task, "contract_authority_sequence", 0) or 0
            ),
            "r2_sentinel_present": any(
                str(item.get("id") or item.get("acceptance_id") or "")
                == "AC-R2-SENTINEL"
                for item in final_task.contract.acceptance_criteria
                if isinstance(item, Mapping)
            ),
        },
        "late_r1_result": {
            "status": admission.status,
            "admitted": admission.admitted,
            "issue_codes": sorted({
                str(issue.get("code") or "") for issue in admission.issues
            }),
        },
        "current_result": {
            "task_map_generation": current_generation,
            "status": current_admission.status,
            "admitted": current_admission.admitted,
            "issue_codes": sorted({
                str(issue.get("code") or "")
                for issue in current_admission.issues
            }),
        },
        "terminal": {
            "type": terminal.type if terminal is not None else "",
            "reason": str((terminal.payload or {}).get("reason") or "")
            if terminal is not None else "",
        },
        "event_counts": dict(sorted(counts.items())),
        "rework_count": sum(
            count for event_type, count in counts.items()
            if "rework" in event_type
        ),
        "replan_count": sum(
            count for event_type, count in counts.items()
            if "replan" in event_type
        ),
    }
    _write_json(args.report.resolve(), report)
    return report


def main() -> int:
    args = _parser().parse_args()
    report = run(args)
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
