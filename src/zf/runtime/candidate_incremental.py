"""Incremental, per-Task Candidate integration for Task Pipeline v4."""

from __future__ import annotations

import hashlib
import json
import subprocess
import time
from pathlib import Path
from typing import Any, Mapping

from zf.core.events.model import ZfEvent
from zf.core.events.writer import EventWriter
from zf.core.state.locks import locked_path
from zf.core.verification.evidence import command_evidence
from zf.runtime.call_result_envelope import write_immutable_json_sidecar
from zf.runtime.candidates import CandidateRebuilder, CandidateResult
from zf.runtime.git_capture import git_env
from zf.runtime.sidecar_refs import hydrate_sidecar_ref
from zf.runtime.task_pipeline_identity import task_pipeline_operation_identity
from zf.runtime.verification_commands import task_contract_verification_commands
from zf.runtime.workflow_operation import WorkflowOperationService


TASK_INTEGRATION_RECEIPT_SCHEMA = "task-integration-receipt.v1"
ROLLING_SMOKE_RECEIPT_SCHEMA = "rolling-smoke-command-receipt.v1"


class CandidateIncrementalError(RuntimeError):
    """The per-Task integration contract could not be admitted."""


def hydrate_task_integration_receipt(
    rebuilder: CandidateRebuilder,
    descriptor: Mapping[str, Any],
    *,
    task_id: str = "",
    workflow_run_id: str = "",
    task_map_generation: str = "",
) -> dict[str, Any]:
    """Hydrate and currentness-check one immutable integration receipt."""

    body = hydrate_sidecar_ref(
        rebuilder.state_dir,
        dict(descriptor),
    ).payload
    if not isinstance(body, Mapping):
        raise CandidateIncrementalError("integration receipt is not an object")
    receipt = dict(body)
    required = (
        "workflow_run_id",
        "task_id",
        "task_map_generation",
        "pipeline_key",
        "integration_operation_id",
        "task_ref",
        "task_commit",
        "contract_revision",
        "candidate_generation",
        "expected_candidate_head",
        "new_candidate_head",
        "patch_identity",
        "command_registry_digest",
    )
    if any(not str(receipt.get(key) or "").strip() for key in required):
        raise CandidateIncrementalError("integration receipt is incomplete")
    expected = {
        "task_id": task_id,
        "workflow_run_id": workflow_run_id,
        "task_map_generation": task_map_generation,
    }
    for key, value in expected.items():
        if value and str(receipt.get(key) or "") != value:
            raise CandidateIncrementalError(
                f"integration receipt currentness mismatch: {key}"
            )
    index = rebuilder._task_index().get(str(receipt["task_id"]), {})
    if (
        not isinstance(index, Mapping)
        or str(index.get("task_ref") or "") != str(receipt["task_ref"])
        or str(index.get("source_commit") or "") != str(receipt["task_commit"])
    ):
        raise CandidateIncrementalError("integration receipt TaskRef is stale")
    refs = receipt.get("rolling_smoke_receipt_refs")
    if not isinstance(refs, list) or not refs:
        raise CandidateIncrementalError(
            "integration receipt has no rolling smoke evidence"
        )
    for item in refs:
        if not isinstance(item, Mapping):
            raise CandidateIncrementalError("rolling smoke receipt ref is invalid")
        hydrate_sidecar_ref(rebuilder.state_dir, dict(item))
    branch = str(receipt.get("candidate_branch") or "")
    if not branch:
        raise CandidateIncrementalError("integration receipt candidate branch missing")
    current = _resolve_commit(rebuilder.project_root, f"refs/heads/{branch}")
    integrated_head = str(receipt["new_candidate_head"])
    ancestor = subprocess.run(
        ["git", "merge-base", "--is-ancestor", integrated_head, current],
        cwd=rebuilder.project_root,
        capture_output=True,
        check=False,
        env=git_env(),
    )
    if ancestor.returncode != 0:
        raise CandidateIncrementalError(
            "integration receipt candidate head is not current ancestry"
        )
    return receipt


def integrate_task_candidate(
    rebuilder: CandidateRebuilder,
    *,
    task_id: str,
    workflow_run_id: str,
    task_map_generation: str,
    operation_generation: int,
    pipeline_key: str,
    dispatch_base_commit: str,
    contract_revision: str,
    event_writer: EventWriter,
    causation_id: str = "",
) -> CandidateResult:
    """Integrate one exact TaskRef under the existing Candidate lock/CAS."""

    pdd_id = rebuilder.pdd_id_for_task(task_id)
    branch = f"{rebuilder.config.runtime.git.candidate_branch_prefix}/{pdd_id}"
    base_commit = _resolve_commit(rebuilder.project_root, dispatch_base_commit)
    expected_head, branch_exists = _candidate_head(
        rebuilder.project_root,
        branch=branch,
        base_commit=base_commit,
    )
    identity = task_pipeline_operation_identity(
        workflow_run_id=workflow_run_id,
        task_id=task_id,
        task_map_generation=task_map_generation,
        stage="integration",
        stage_revision=TASK_INTEGRATION_RECEIPT_SCHEMA,
        operation_generation=operation_generation,
    )
    replay = _integrated_event(rebuilder, identity.operation_id)
    if replay is not None:
        replay_payload = dict(replay.payload)
        descriptor = replay_payload.get("receipt_ref")
        if not isinstance(descriptor, Mapping):
            raise CandidateIncrementalError(
                "integrated event has no immutable receipt ref"
            )
        receipt = hydrate_sidecar_ref(
            rebuilder.state_dir,
            dict(descriptor),
        ).payload
        if (
            not isinstance(receipt, Mapping)
            or str(receipt.get("integration_operation_id") or "")
            != identity.operation_id
            or _resolve_commit(rebuilder.project_root, f"refs/heads/{branch}")
            != str(receipt.get("new_candidate_head") or "")
        ):
            raise CandidateIncrementalError(
                "replayed integration receipt is not current"
            )
        return CandidateResult(
            status="integrated",
            event_type="integration.queue.integrated",
            payload=replay_payload,
        )
    request = {
        "workflow_run_id": workflow_run_id,
        "task_id": task_id,
        "task_pipeline_stage": "integration",
        "operation_generation": operation_generation,
        "task_map_generation": task_map_generation,
        "pipeline_key": pipeline_key or identity.pipeline_key,
        "candidate_generation": _candidate_generation(
            workflow_run_id,
            task_map_generation,
        ),
        "candidate_branch": branch,
        "expected_candidate_head": expected_head,
        "dispatch_base_commit": base_commit,
        "contract_revision": contract_revision,
        "integration_strategy": rebuilder.config.runtime.git.candidate_strategy,
        "partial_candidate_auto_ship": "forbidden",
    }
    operations = WorkflowOperationService(
        state_dir=rebuilder.state_dir,
        event_log=rebuilder.event_log,
        event_writer=event_writer,
    )
    ensured = operations.ensure_operation(
        workflow_run_id=workflow_run_id,
        operation_id=identity.operation_id,
        operation_type="task-stage",
        request=request,
        parent_stage_id="integration",
        task_id=task_id,
        role_instance="candidate-integrator",
        causation_id=causation_id,
        correlation_id=workflow_run_id,
    )
    if ensured.status == "settled":
        replay = _integrated_event(rebuilder, identity.operation_id)
        if replay is None:
            raise CandidateIncrementalError(
                "settled integration operation has no admitted receipt event"
            )
        return CandidateResult(
            status="integrated",
            event_type="integration.queue.integrated",
            payload=dict(replay.payload),
        )
    if ensured.status in {"blocked", "failed", "cancelled", "superseded"}:
        raise CandidateIncrementalError(
            f"integration operation is terminal: {ensured.status}"
        )
    if ensured.status == "divergent":
        raise CandidateIncrementalError("integration operation request diverged")

    _emit_once(
        rebuilder,
        event_writer,
        "task.integration_enqueued",
        operation_id=identity.operation_id,
        task_id=task_id,
        payload={
            **request,
            "operation_id": identity.operation_id,
            "operation_key": identity.operation_key,
            "queue_entry_id": identity.operation_id,
            "source_ref": _task_ref(rebuilder, task_id),
            "base_ref": expected_head,
        },
        causation_id=causation_id,
    )
    operations.mark_started(
        operation_id=identity.operation_id,
        request_hash=ensured.request_hash,
        workflow_run_id=workflow_run_id,
        task_id=task_id,
        dispatch_id=identity.operation_id,
        role_instance="candidate-integrator",
        active_attempt_id=identity.operation_id,
        lease_id=identity.operation_id,
        causation_id=causation_id,
        correlation_id=workflow_run_id,
    )
    integrating = _emit_once(
        rebuilder,
        event_writer,
        "integration.queue.integrating",
        operation_id=identity.operation_id,
        task_id=task_id,
        payload={
            **request,
            "operation_id": identity.operation_id,
            "operation_key": identity.operation_key,
            "queue_entry_id": identity.operation_id,
        },
        causation_id=causation_id,
    )

    try:
        with locked_path(rebuilder._manifest_path(pdd_id)):
            receipt, receipt_ref = _integrate_locked(
                rebuilder,
                task_id=task_id,
                workflow_run_id=workflow_run_id,
                task_map_generation=task_map_generation,
                operation_generation=operation_generation,
                operation_id=identity.operation_id,
                pipeline_key=pipeline_key or identity.pipeline_key,
                candidate_generation=request["candidate_generation"],
                branch=branch,
                branch_exists=branch_exists,
                expected_head=expected_head,
                base_commit=base_commit,
                contract_revision=contract_revision,
                source_event_id=integrating.id if integrating is not None else causation_id,
            )
    except Exception as exc:
        operations.block(
            operation_id=identity.operation_id,
            request_hash=ensured.request_hash,
            workflow_run_id=workflow_run_id,
            task_id=task_id,
            reason=f"candidate_incremental_failed:{type(exc).__name__}",
            details={"detail": str(exc)[:500]},
            causation_id=integrating.id if integrating is not None else causation_id,
            correlation_id=workflow_run_id,
        )
        failed = _emit_once(
            rebuilder,
            event_writer,
            "integration.queue.needs_review",
            operation_id=identity.operation_id,
            task_id=task_id,
            payload={
                **request,
                "operation_id": identity.operation_id,
                "queue_entry_id": identity.operation_id,
                "reason": f"{type(exc).__name__}: {exc}"[:500],
            },
            causation_id=integrating.id if integrating is not None else causation_id,
        )
        return CandidateResult(
            status="needs_review",
            event_type="integration.queue.needs_review",
            payload=dict(failed.payload) if failed is not None else {},
        )

    settled = operations.settle(
        operation_id=identity.operation_id,
        request_hash=ensured.request_hash,
        workflow_run_id=workflow_run_id,
        task_id=task_id,
        admitted_call_result_ref=receipt_ref,
        causation_id=integrating.id if integrating is not None else causation_id,
        correlation_id=workflow_run_id,
    )
    payload = {
        "schema_version": TASK_INTEGRATION_RECEIPT_SCHEMA,
        "workflow_run_id": workflow_run_id,
        "task_id": task_id,
        "task_map_generation": task_map_generation,
        "pipeline_key": pipeline_key or identity.pipeline_key,
        "operation_id": identity.operation_id,
        "operation_generation": operation_generation,
        "queue_entry_id": identity.operation_id,
        "candidate_generation": request["candidate_generation"],
        "candidate_branch": branch,
        "candidate_head": receipt["new_candidate_head"],
        "task_ref": receipt["task_ref"],
        "task_commit": receipt["task_commit"],
        "receipt_ref": receipt_ref,
        "receipt_digest": str(receipt_ref.get("sha256") or ""),
        "status": "integrated",
    }
    integrated = _emit_once(
        rebuilder,
        event_writer,
        "integration.queue.integrated",
        operation_id=identity.operation_id,
        task_id=task_id,
        payload=payload,
        causation_id=settled.id if settled is not None else (
            integrating.id if integrating is not None else causation_id
        ),
    )
    _emit_once(
        rebuilder,
        event_writer,
        "candidate.updated",
        operation_id=identity.operation_id,
        task_id=task_id,
        payload={
            **payload,
            "incremental": True,
            "integrated_task_id": task_id,
        },
        causation_id=integrated.id if integrated is not None else causation_id,
    )
    return CandidateResult(
        status="integrated",
        event_type="integration.queue.integrated",
        payload=payload,
    )


def _integrate_locked(
    rebuilder: CandidateRebuilder,
    *,
    task_id: str,
    workflow_run_id: str,
    task_map_generation: str,
    operation_generation: int,
    operation_id: str,
    pipeline_key: str,
    candidate_generation: str,
    branch: str,
    branch_exists: bool,
    expected_head: str,
    base_commit: str,
    contract_revision: str,
    source_event_id: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    current_head, current_exists = _candidate_head(
        rebuilder.project_root,
        branch=branch,
        base_commit=base_commit,
    )
    if current_head != expected_head or current_exists != branch_exists:
        raise CandidateIncrementalError("candidate_head_cas_mismatch")
    tasks = rebuilder.tasks_from_index(
        rebuilder.pdd_id_for_task(task_id),
        [task_id],
    )
    if len(tasks) != 1:
        raise CandidateIncrementalError("exact TaskRef is missing from task index")
    task_ref = tasks[0]
    stale = rebuilder._stale_task_refs(tasks)
    if stale:
        raise CandidateIncrementalError("TaskRef currentness check failed")
    if rebuilder.config.runtime.git.candidate_strategy != "cherry-pick":
        raise CandidateIncrementalError("v4 incremental integration requires cherry-pick")

    pdd_id = rebuilder.pdd_id_for_task(task_id)
    rebuilder._prepare_worktree(pdd_id, expected_head)
    worktree = rebuilder._worktree_path(pdd_id)
    declared_files = rebuilder._candidate_task_scope_files(expected_head, task_ref)
    commits, skipped_commits = rebuilder._task_commits(
        expected_head,
        task_ref,
        declared_files=declared_files,
    )
    applied_commits: list[str] = []
    for commit in commits:
        status = rebuilder._apply_task_commit(
            worktree,
            commit,
            declared_files=declared_files,
        )
        if status == "applied":
            applied_commits.append(commit)
        else:
            skipped_commits.append(commit)
    new_head = _resolve_commit(worktree, "HEAD")
    environment = rebuilder._prepare_candidate_environment(
        worktree=worktree,
        commit=new_head,
    )
    if environment.get("status") != "ready":
        raise CandidateIncrementalError(
            f"candidate environment failed: {environment.get('detail')}"
        )
    smoke_refs, registry_digest = _run_rolling_smoke(
        rebuilder,
        task_id=task_id,
        worktree=worktree,
        target_commit=new_head,
        source_event_id=source_event_id,
    )
    dirty = _git(rebuilder.project_root, "-C", str(worktree), "status", "--porcelain")
    if dirty:
        raise CandidateIncrementalError("rolling smoke left candidate worktree dirty")
    _update_candidate_ref(
        rebuilder.project_root,
        branch=branch,
        new_head=new_head,
        expected_head=expected_head,
        branch_exists=branch_exists,
    )
    patch_identity = _patch_identity(
        rebuilder.project_root,
        commits or [task_ref.source_commit],
    )
    receipt = {
        "schema_version": TASK_INTEGRATION_RECEIPT_SCHEMA,
        "workflow_run_id": workflow_run_id,
        "task_id": task_id,
        "task_map_generation": task_map_generation,
        "pipeline_key": pipeline_key,
        "integration_operation_id": operation_id,
        "operation_generation": operation_generation,
        "task_ref": task_ref.task_ref,
        "task_commit": task_ref.source_commit,
        "contract_revision": contract_revision,
        "candidate_generation": candidate_generation,
        "candidate_branch": branch,
        "expected_candidate_head": expected_head,
        "previous_candidate_head": expected_head,
        "new_candidate_head": new_head,
        "integration_strategy": "cherry-pick",
        "patch_identity": patch_identity,
        "applied_commits": applied_commits,
        "skipped_commits": skipped_commits,
        "rolling_smoke_receipt_refs": smoke_refs,
        "command_registry_digest": registry_digest,
        "candidate_environment": environment,
        "status": "integrated",
    }
    descriptor = write_immutable_json_sidecar(
        rebuilder.state_dir,
        receipt,
        root="task-integration-receipts",
        kind="task_integration_receipt",
        schema_version=TASK_INTEGRATION_RECEIPT_SCHEMA,
        created_by="candidate-integrator",
        source_event_id=source_event_id,
    )
    _validate_receipt(
        rebuilder,
        descriptor,
        expected=receipt,
        branch=branch,
    )
    return receipt, descriptor


def _run_rolling_smoke(
    rebuilder: CandidateRebuilder,
    *,
    task_id: str,
    worktree: Path,
    target_commit: str,
    source_event_id: str,
) -> tuple[list[dict[str, Any]], str]:
    task = rebuilder.task_store.get(task_id)
    commands = (
        task_contract_verification_commands(task.contract)
        if task is not None
        else []
    )
    registry_digest = _digest(commands)
    selected = [
        command
        for command in commands
        if command.get("rolling_smoke") is True
    ]
    if not selected:
        raise CandidateIncrementalError("rolling_smoke_command_missing")
    refs: list[dict[str, Any]] = []
    from zf.runtime.candidates import _quality_gate_env

    for command in selected:
        started = time.monotonic()
        timeout = max(1, min(int(command.get("timeout_seconds") or 900), 3600))
        try:
            result = subprocess.run(
                str(command["command"]),
                shell=True,
                cwd=worktree,
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
                env=_quality_gate_env(worktree),
            )
            evidence = command_evidence(
                command=str(command["command"]),
                exit_code=result.returncode,
                stdout=(result.stdout or "")[-100_000:],
                stderr=(result.stderr or "")[-100_000:],
            )
        except subprocess.TimeoutExpired as exc:
            evidence = command_evidence(
                command=str(command["command"]),
                exit_code=None,
                stdout=str(exc.stdout or "")[-100_000:],
                stderr=str(exc.stderr or "")[-100_000:],
                timed_out=True,
            )
        evidence.update({
            "schema_version": ROLLING_SMOKE_RECEIPT_SCHEMA,
            "command_id": str(command["id"]),
            "command_digest": str(command["command_digest"]),
            "command_registry_digest": registry_digest,
            "execution_root": str(worktree),
            "target_commit": target_commit,
            "duration_ms": int((time.monotonic() - started) * 1000),
        })
        descriptor = write_immutable_json_sidecar(
            rebuilder.state_dir,
            evidence,
            root="rolling-smoke-receipts",
            kind="rolling_smoke_command_receipt",
            schema_version=ROLLING_SMOKE_RECEIPT_SCHEMA,
            created_by="candidate-integrator",
            source_event_id=source_event_id,
        )
        refs.append(descriptor)
        if evidence.get("exit_code") != 0:
            raise CandidateIncrementalError(
                f"rolling smoke failed: {command['id']}"
            )
    return refs, registry_digest


def _validate_receipt(
    rebuilder: CandidateRebuilder,
    descriptor: Mapping[str, Any],
    *,
    expected: Mapping[str, Any],
    branch: str,
) -> None:
    body = hydrate_task_integration_receipt(rebuilder, descriptor)
    if dict(body) != dict(expected):
        raise CandidateIncrementalError("integration receipt content diverged")
    current = _resolve_commit(rebuilder.project_root, f"refs/heads/{branch}")
    if current != str(body.get("new_candidate_head") or ""):
        raise CandidateIncrementalError("integration receipt candidate head is stale")


def _emit_once(
    rebuilder: CandidateRebuilder,
    event_writer: EventWriter,
    event_type: str,
    *,
    operation_id: str,
    task_id: str,
    payload: Mapping[str, Any],
    causation_id: str,
) -> ZfEvent | None:
    for event in reversed(rebuilder.event_log.read_all()):
        if event.type != event_type or event.task_id != task_id:
            continue
        body = event.payload if isinstance(event.payload, dict) else {}
        if str(body.get("operation_id") or "") == operation_id:
            return event
    return event_writer.append(ZfEvent(
        type=event_type,
        actor="zf-cli",
        origin="kernel",
        task_id=task_id,
        payload=dict(payload),
        causation_id=causation_id or None,
        correlation_id=str(payload.get("workflow_run_id") or "") or None,
    ))


def _integrated_event(
    rebuilder: CandidateRebuilder,
    operation_id: str,
) -> ZfEvent | None:
    for event in reversed(rebuilder.event_log.read_all()):
        if event.type != "integration.queue.integrated":
            continue
        payload = event.payload if isinstance(event.payload, dict) else {}
        if str(payload.get("operation_id") or "") == operation_id:
            return event
    return None


def _task_ref(rebuilder: CandidateRebuilder, task_id: str) -> str:
    entry = rebuilder._task_index().get(task_id, {})
    return str(entry.get("task_ref") or "") if isinstance(entry, dict) else ""


def _candidate_head(
    project_root: Path,
    *,
    branch: str,
    base_commit: str,
) -> tuple[str, bool]:
    result = subprocess.run(
        ["git", "rev-parse", "--verify", f"refs/heads/{branch}^{{commit}}"],
        cwd=project_root,
        capture_output=True,
        text=True,
        check=False,
        env=git_env(),
    )
    if result.returncode == 0:
        return result.stdout.strip(), True
    return base_commit, False


def _update_candidate_ref(
    project_root: Path,
    *,
    branch: str,
    new_head: str,
    expected_head: str,
    branch_exists: bool,
) -> None:
    expected_ref = expected_head if branch_exists else "0" * 40
    result = subprocess.run(
        ["git", "update-ref", f"refs/heads/{branch}", new_head, expected_ref],
        cwd=project_root,
        capture_output=True,
        text=True,
        check=False,
        env=git_env(),
    )
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip()
        raise CandidateIncrementalError(f"candidate_head_cas_mismatch: {detail}")


def _patch_identity(project_root: Path, commits: list[str]) -> str:
    patch_ids: list[str] = []
    for commit in commits:
        shown = subprocess.run(
            ["git", "show", "--pretty=format:", "--binary", commit],
            cwd=project_root,
            capture_output=True,
            check=False,
            env=git_env(),
        )
        if shown.returncode != 0:
            continue
        result = subprocess.run(
            ["git", "patch-id", "--stable"],
            cwd=project_root,
            input=shown.stdout,
            capture_output=True,
            check=False,
            env=git_env(),
        )
        if result.returncode == 0 and result.stdout.strip():
            patch_ids.append(result.stdout.decode("utf-8").split()[0])
    return _digest(patch_ids or commits)


def _resolve_commit(cwd: Path, ref: str) -> str:
    value = _git(cwd, "rev-parse", "--verify", f"{ref}^{{commit}}")
    if not value:
        raise CandidateIncrementalError(f"git ref is missing: {ref}")
    return value


def _git(cwd: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
        env=git_env(),
    )
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip()
        raise CandidateIncrementalError(
            f"git {' '.join(args)} failed: {detail}"
        )
    return result.stdout.strip()


def _candidate_generation(workflow_run_id: str, task_map_generation: str) -> str:
    return "cg-" + hashlib.sha256(
        f"{workflow_run_id}|{task_map_generation}".encode("utf-8")
    ).hexdigest()[:16]


def _digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode("utf-8")
    ).hexdigest()


__all__ = [
    "CandidateIncrementalError",
    "ROLLING_SMOKE_RECEIPT_SCHEMA",
    "TASK_INTEGRATION_RECEIPT_SCHEMA",
    "hydrate_task_integration_receipt",
    "integrate_task_candidate",
]
