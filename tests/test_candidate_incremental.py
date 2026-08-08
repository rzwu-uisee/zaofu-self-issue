from __future__ import annotations

import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from zf.core.config.schema import (
    GitIsolationConfig,
    ProjectConfig,
    RuntimeConfig,
    WorkdirConfig,
    ZfConfig,
)
from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.core.events.writer import EventWriter
from zf.core.state.role_sessions import RoleSessionRegistry
from zf.core.task.schema import Task
from zf.core.task.store import TaskStore
from zf.runtime.candidates import CandidateRebuilder
from zf.runtime.candidate_incremental import (
    CandidateIncrementalError,
    _update_candidate_ref,
)
from zf.runtime.sidecar_refs import hydrate_sidecar_ref
from zf.runtime.task_refs import TaskRefManager
from zf.runtime.task_pipeline_terminal import (
    _release_archived_task_stage_slot,
    reconcile_task_pipeline_freeze,
    reconcile_task_pipeline_terminals,
)
from zf.runtime.tmux import TmuxError


def _git(cwd: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


def _setup(tmp_path: Path, *, rolling_smoke: bool = True):
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.name", "Integration Test")
    _git(tmp_path, "config", "user.email", "integration@example.com")
    (tmp_path / "README.md").write_text("base\n", encoding="utf-8")
    _git(tmp_path, "add", "README.md")
    _git(tmp_path, "commit", "-q", "-m", "base")
    _git(tmp_path, "branch", "-M", "main")
    base = _git(tmp_path, "rev-parse", "HEAD")

    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    (state_dir / "kanban.json").write_text("[]\n", encoding="utf-8")
    config = ZfConfig(
        project=ProjectConfig(name="incremental", state_dir=str(state_dir)),
        runtime=RuntimeConfig(
            workdirs=WorkdirConfig(enabled=True, mode="worktree"),
            git=GitIsolationConfig(
                candidate_base_ref="main",
                auto_ship_on_candidate_complete=True,
            ),
        ),
    )
    log = EventLog(state_dir / "events.jsonl")
    writer = EventWriter(log)
    task = Task(id="TASK-A", title="Task A", key="F-11111111:TASK-A")
    task.contract.contract_revision = "contract-r1"
    task.contract.scope = ["feature.txt"]
    task.contract.validation = {
        "commands": [{
            "id": "rolling-file",
            "command": "test -f feature.txt",
            "tier": "runtime",
            "rolling_smoke": rolling_smoke,
        }]
    }
    TaskStore(state_dir / "kanban.json").add(task)
    writer.append(ZfEvent(
        type="task.created",
        task_id=task.id,
        payload={"feature_id": "F-11111111"},
    ))

    _git(tmp_path, "checkout", "-q", "-b", "worker/task-a", "main")
    (tmp_path / "feature.txt").write_text("implemented\n", encoding="utf-8")
    _git(tmp_path, "add", "feature.txt")
    _git(tmp_path, "commit", "-q", "-m", "feat: task a")
    task_commit = _git(tmp_path, "rev-parse", "HEAD")
    ref_result = TaskRefManager(
        state_dir=state_dir,
        project_root=tmp_path,
        config=config,
    ).process_dev_build_done(ZfEvent(
        type="dev.build.done",
        actor="impl-1",
        task_id=task.id,
        payload={
            "source_commit": task_commit,
            "source_branch": "worker/task-a",
            "feature_id": "F-11111111",
            "files_touched": ["feature.txt"],
        },
    ))
    assert ref_result is not None and ref_result.status == "updated"
    return state_dir, config, log, writer, base, task_commit


def _integrate(
    root: Path,
    state_dir: Path,
    config: ZfConfig,
    log: EventLog,
    writer: EventWriter,
    base: str,
):
    return CandidateRebuilder(
        state_dir=state_dir,
        project_root=root,
        config=config,
        event_log=log,
    ).integrate_task_pipeline_task(
        task_id="TASK-A",
        workflow_run_id="run-1",
        task_map_generation="map-g1",
        operation_generation=1,
        pipeline_key="pipeline-A",
        dispatch_base_commit=base,
        contract_revision="contract-r1",
        event_writer=writer,
        causation_id="generation-event",
    )


def test_incremental_integration_emits_receipt_without_legacy_delivery_events(
    tmp_path: Path,
) -> None:
    state_dir, config, log, writer, base, _ = _setup(tmp_path)

    result = _integrate(tmp_path, state_dir, config, log, writer, base)
    events = log.read_all()
    integrated = [
        event for event in events
        if event.type == "integration.queue.integrated"
    ]

    assert result.status == "integrated"
    assert len(integrated) == 1
    assert not [
        event for event in events
        if event.type in {"candidate.integration.completed", "candidate.ready"}
    ]
    receipt_ref = integrated[0].payload["receipt_ref"]
    receipt = hydrate_sidecar_ref(state_dir, receipt_ref).payload
    assert receipt["schema_version"] == "task-integration-receipt.v1"
    assert receipt["status"] == "integrated"
    assert receipt["new_candidate_head"] == _git(
        tmp_path,
        "rev-parse",
        "refs/heads/candidate/F-11111111",
    )
    assert receipt["rolling_smoke_receipt_refs"]
    assert TaskStore(state_dir / "kanban.json").get("TASK-A").status == "backlog"


def test_incremental_integration_replay_is_idempotent(tmp_path: Path) -> None:
    state_dir, config, log, writer, base, _ = _setup(tmp_path)

    first = _integrate(tmp_path, state_dir, config, log, writer, base)
    second = _integrate(tmp_path, state_dir, config, log, writer, base)
    events = log.read_all()

    assert first.payload["receipt_digest"] == second.payload["receipt_digest"]
    assert len([
        event for event in events
        if event.type == "integration.queue.integrated"
    ]) == 1
    assert len([
        event for event in events if event.type == "candidate.updated"
    ]) == 1


def test_candidate_head_cas_conflict_never_overwrites_current_head(
    tmp_path: Path,
) -> None:
    _state_dir, _config, _log, _writer, base, task_commit = _setup(tmp_path)
    branch = "candidate/F-11111111"
    _update_candidate_ref(
        tmp_path,
        branch=branch,
        new_head=task_commit,
        expected_head=base,
        branch_exists=False,
    )

    with pytest.raises(
        CandidateIncrementalError,
        match="candidate_head_cas_mismatch",
    ):
        _update_candidate_ref(
            tmp_path,
            branch=branch,
            new_head=base,
            expected_head=base,
            branch_exists=True,
        )

    assert _git(tmp_path, "rev-parse", f"refs/heads/{branch}") == task_commit


def test_incremental_integration_fails_closed_without_marked_rolling_smoke(
    tmp_path: Path,
) -> None:
    state_dir, config, log, writer, base, _ = _setup(
        tmp_path,
        rolling_smoke=False,
    )

    result = _integrate(tmp_path, state_dir, config, log, writer, base)
    events = log.read_all()

    assert result.status == "needs_review"
    assert [
        event for event in events
        if event.type == "integration.queue.needs_review"
    ]
    assert not [
        event for event in events
        if event.type in {
            "integration.queue.integrated",
            "candidate.updated",
            "candidate.integration.completed",
            "candidate.ready",
        }
    ]


def test_integration_receipt_drives_task_terminal_and_exact_candidate_freeze(
    tmp_path: Path,
) -> None:
    state_dir, config, log, writer, base, _ = _setup(tmp_path)
    generation = writer.append(ZfEvent(
        type="task.pipeline.generation.admitted",
        origin="kernel",
        payload={
            "schema_version": "task-pipeline-generation.v1",
            "generation_id": "generation-1",
            "workflow_run_id": "run-1",
            "flow_kind": "issue",
            "request_kind": "issue",
            "pdd_id": "F-11111111",
            "feature_id": "F-11111111",
            "fanout_id": "fanout-plan-1",
            "task_map_generation": "map-g1",
            "plan_artifact_package_id": "planpkg-1",
            "plan_artifact_package_ref": "artifacts/plan-packages/planpkg-1.json",
            "plan_artifact_package_digest": "planpkg-digest-1",
            "task_map_ref": "artifacts/plan/task-map.json",
            "task_map_digest": "task-map-digest-1",
            "source_index_ref": "artifacts/plan/source-index.json",
            "dispatch_base_commit": base,
            "task_ids": ["TASK-A"],
        },
        correlation_id="run-1",
    ))
    _integrate(tmp_path, state_dir, config, log, writer, base)
    sessions = RoleSessionRegistry(
        state_dir / "role_sessions.yaml", project_root=str(tmp_path)
    )
    binding = sessions.bind_task_stage_session(
        workflow_run_id="run-1",
        task_id="TASK-A",
        stage="impl",
        rework_affinity_id="map-g1:impl",
        role_instance="impl-1",
        role_config_digest="config-sha",
        workspace_generation=1,
        placement_epoch=1,
        backend="mock",
    )
    sessions.activate_task_stage_session(
        binding_key=binding["binding_key"], role_instance="impl-1"
    )
    context = {
        **generation.payload,
        "generation_admitted_event_id": generation.id,
    }
    terminated: list[str] = []
    role = SimpleNamespace(instance_id="impl-1")
    runtime = SimpleNamespace(
        state_dir=state_dir,
        project_root=tmp_path,
        config=config,
        event_log=log,
        event_writer=writer,
        task_store=TaskStore(state_dir / "kanban.json"),
        transport=SimpleNamespace(
            is_alive=lambda _instance: True,
            terminate=lambda instance: terminated.append(instance),
        ),
        _find_role_by_instance=lambda instance: (
            role if instance == role.instance_id else None
        ),
        _set_worker_state=lambda *_args, **_kwargs: None,
        _emit_role_lifecycle_event=lambda *_args, **_kwargs: None,
    )

    terminal = reconcile_task_pipeline_terminals(
        runtime,
        generation_contexts={"TASK-A": context},
    )
    frozen = reconcile_task_pipeline_freeze(
        runtime,
        generation_contexts={"TASK-A": context},
    )
    replay_terminal = reconcile_task_pipeline_terminals(
        runtime,
        generation_contexts={"TASK-A": context},
    )
    replay_freeze = reconcile_task_pipeline_freeze(
        runtime,
        generation_contexts={"TASK-A": context},
    )
    events = log.read_all()

    assert [decision.action for decision in terminal] == [
        "task_pipeline_task_done",
        "task_pipeline_sessions_archived",
    ]
    assert [decision.action for decision in frozen] == [
        "task_pipeline_candidate_frozen"
    ]
    assert replay_terminal == []
    assert replay_freeze == []
    assert runtime.task_store.get("TASK-A").status == "done"
    assert len([event for event in events if event.type == "task.done"]) == 1
    archived = RoleSessionRegistry(
        state_dir / "role_sessions.yaml", project_root=str(tmp_path)
    )
    assert archived.task_stage_binding(
        workflow_run_id="run-1",
        task_id="TASK-A",
        stage="impl",
        rework_affinity_id="map-g1:impl",
    )["status"] == "archived"
    assert (
        archived.instance_meta()["impl-1"]["active_task_stage_binding_key"]
        == ""
    )
    assert str(archived.get("impl-1")) != binding["session_id"]
    assert terminated == ["impl-1"]
    assert len([
        event for event in events
        if event.type == "task.pipeline.sessions.archived"
    ]) == 1
    ready = [event for event in events if event.type == "candidate.ready"]
    assert len(ready) == 1
    assert ready[0].payload["commit"] == _git(
        tmp_path,
        "rev-parse",
        "refs/heads/candidate/F-11111111",
    )
    assert ready[0].payload["fanout_id"] == "fanout-plan-1"
    assert ready[0].payload["flow_kind"] == "issue"
    assert ready[0].payload["request_kind"] == "issue"
    assert ready[0].payload["pdd_id"] == "F-11111111"
    assert ready[0].payload["feature_id"] == "F-11111111"
    assert ready[0].payload["candidate_base_commit"] == base
    assert ready[0].payload["candidate_head_commit"] == ready[0].payload["commit"]
    assert ready[0].payload["diff_ref"] == (
        f"{base}..{ready[0].payload['commit']}"
    )
    assert ready[0].payload["completed_task_ids"] == ["TASK-A"]
    assert ready[0].payload["plan_artifact_package_id"] == "planpkg-1"
    assert ready[0].payload["task_map_ref"] == "artifacts/plan/task-map.json"
    assert ready[0].payload["source_index_ref"] == (
        "artifacts/plan/source-index.json"
    )
    freeze = hydrate_sidecar_ref(
        state_dir,
        ready[0].payload["freeze_receipt_ref"],
    ).payload
    assert freeze["schema_version"] == "candidate-freeze-receipt.v1"
    assert freeze["fanout_id"] == "fanout-plan-1"
    assert freeze["flow_kind"] == "issue"
    assert freeze["candidate_base_commit"] == base
    assert freeze["task_ids"] == ["TASK-A"]
    assert freeze["plan_artifact_package_id"] == "planpkg-1"
    assert freeze["task_map_ref"] == "artifacts/plan/task-map.json"
    assert freeze["source_index_ref"] == "artifacts/plan/source-index.json"


@pytest.mark.parametrize(
    ("alive_after_failure", "released", "failure_count"),
    [(False, True, 0), (True, False, 1)],
)
def test_archived_slot_release_reprobes_after_safe_terminate_rejection(
    tmp_path: Path,
    alive_after_failure: bool,
    released: bool,
    failure_count: int,
) -> None:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    log = EventLog(state_dir / "events.jsonl")
    writer = EventWriter(log)
    registry = RoleSessionRegistry(
        state_dir / "role_sessions.yaml",
        project_root=str(tmp_path),
    )
    binding = registry.bind_task_stage_session(
        workflow_run_id="run-1",
        task_id="TASK-A",
        stage="verify",
        rework_affinity_id="map-g1:verify",
        role_instance="verify-1",
        role_config_digest="config-sha",
        workspace_generation=1,
        placement_epoch=1,
        backend="mock",
    )
    registry.activate_task_stage_session(
        binding_key=binding["binding_key"],
        role_instance="verify-1",
    )
    probes = iter((True, alive_after_failure))

    class _Transport:
        def is_alive(self, _instance: str) -> bool:
            return next(probes)

        def terminate(self, _instance: str) -> None:
            raise TmuxError("target no longer proves role ownership")

    runtime = SimpleNamespace(
        state_dir=state_dir,
        project_root=tmp_path,
        event_writer=writer,
        transport=_Transport(),
        _find_role_by_instance=lambda instance: (
            SimpleNamespace(instance_id=instance)
        ),
        _set_worker_state=lambda *_args, **_kwargs: None,
        _emit_role_lifecycle_event=lambda *_args, **_kwargs: None,
    )

    assert _release_archived_task_stage_slot(
        runtime,
        registry=registry,
        role_instance="verify-1",
        binding_key=binding["binding_key"],
        task_id="TASK-A",
    ) is released
    failures = [
        event for event in log.read_all()
        if event.type == "kernel.housekeeping.failed"
    ]
    assert len(failures) == failure_count
    active_key = str(
        RoleSessionRegistry(
            state_dir / "role_sessions.yaml",
            project_root=str(tmp_path),
        ).instance_meta().get("verify-1", {}).get(
            "active_task_stage_binding_key"
        ) or ""
    )
    assert (active_key == "") is released
