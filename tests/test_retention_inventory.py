from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import yaml

from zf.cli import state as state_cli
from zf.core.events.model import ZfEvent
from zf.runtime.retention_inventory import build_retention_inventory
from zf.runtime.sidecar_refs import write_sidecar_json


def _write_event(path: Path, event: ZfEvent) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(event.to_json() + "\n")


def _item_by_path(report: dict, path: str) -> dict:
    return next(
        item
        for group in ("candidates", "protected", "blocked")
        for item in report[group]
        if item["path"] == path
    )


def test_mixed_inventory_protects_truth_active_and_audit_evidence(
    tmp_path: Path,
) -> None:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    (state_dir / "kanban.json").write_text(json.dumps([{
        "id": "TASK-ACTIVE",
        "title": "active",
        "status": "in_progress",
        "assigned_to": "dev-1",
    }]), encoding="utf-8")
    (state_dir / "feature_list.json").write_text("[]\n", encoding="utf-8")
    (state_dir / "session.yaml").write_text(
        "runtime_state: running\nsession_id: sess-1\n",
        encoding="utf-8",
    )
    (state_dir / "role_sessions.yaml").write_text(yaml.safe_dump({
        "instance_meta": {
            "dev-1": {
                "last_heartbeat_payload": {
                    "current_task_id": "TASK-ACTIVE",
                    "state": "busy",
                },
            },
        },
        "roles": {},
    }), encoding="utf-8")
    (state_dir / "projections").mkdir()
    (state_dir / "projections" / "old.json").write_text(
        '{"rebuildable":true}\n',
        encoding="utf-8",
    )
    active_workdir = state_dir / "workdirs" / "dev-1"
    active_workdir.mkdir(parents=True)
    (active_workdir / "meta.json").write_text(json.dumps({
        "instance_id": "dev-1",
        "role_name": "dev",
    }), encoding="utf-8")
    (active_workdir / "project.txt").write_text("active", encoding="utf-8")
    descriptor = write_sidecar_json(
        state_dir,
        "artifacts/evidence/audit.json",
        {"status": "passed"},
        kind="human_decision_evidence",
        schema_version="human-decision-evidence.v1",
        created_by="test",
        retention={"class": "audit_required"},
    )
    _write_event(state_dir / "events.jsonl", ZfEvent(
        type="run.started",
        id="evt-run",
        payload={"run_id": "run-1", "evidence_ref": descriptor},
    ))

    report = build_retention_inventory(state_dir)

    assert report["status"] == "ready"
    assert _item_by_path(report, "events.jsonl")["status"] == "protected"
    assert _item_by_path(report, "kanban.json")["status"] == "protected"
    assert _item_by_path(report, "workdirs/dev-1/**")["category"] == "active_workdir"
    assert _item_by_path(report, "workdirs/dev-1/**")["status"] == "protected"
    audit = _item_by_path(report, "artifacts/**")
    assert audit["category"] == "audit_required_sidecar"
    assert audit["status"] == "protected"
    projection = _item_by_path(report, "projections/old.json")
    assert projection["status"] == "eligible"
    assert report["totals"]["estimated_reclaim_bytes"] == projection["bytes"]


def test_proven_terminal_workdir_excludes_provider_transcript_from_candidate(
    tmp_path: Path,
) -> None:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    (state_dir / "kanban.json").write_text("[]\n", encoding="utf-8")
    (state_dir / "role_sessions.yaml").write_text(yaml.safe_dump({
        "instance_meta": {"dev-old": {"status": "retired"}},
        "roles": {},
    }), encoding="utf-8")
    workdir = state_dir / "workdirs" / "dev-old"
    session_dir = workdir / "codex-home" / "sessions" / "2026" / "08" / "12"
    session_dir.mkdir(parents=True)
    (workdir / "meta.json").write_text(json.dumps({
        "instance_id": "dev-old",
        "role_name": "dev",
    }), encoding="utf-8")
    (workdir / "project.txt").write_text("terminal", encoding="utf-8")
    (session_dir / "rollout-terminal.jsonl").write_text(
        '{"provider":"codex"}\n',
        encoding="utf-8",
    )
    _write_event(state_dir / "events.jsonl", ZfEvent(
        type="workdir.retired",
        id="evt-retired",
        payload={"instance_id": "dev-old", "status": "removed"},
    ))

    report = build_retention_inventory(state_dir)

    candidate = _item_by_path(report, "workdirs/dev-old/**")
    transcript = _item_by_path(
        report,
        "workdirs/dev-old/**/provider-transcripts",
    )
    assert candidate["category"] == "terminal_workdir"
    assert candidate["status"] == "eligible"
    assert transcript["category"] == "provider_transcript"
    assert transcript["status"] == "protected"
    assert report["totals"]["estimated_reclaim_bytes"] == candidate["bytes"]


def test_dangling_ref_and_broken_manifest_are_blocked(tmp_path: Path) -> None:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    (state_dir / "kanban.json").write_text("[]\n", encoding="utf-8")
    workdir = state_dir / "workdirs" / "dev-old"
    workdir.mkdir(parents=True)
    (workdir / "meta.json").write_text("{broken", encoding="utf-8")
    (workdir / "project.txt").write_text("keep", encoding="utf-8")
    missing_descriptor = {
        "ref_schema_version": "sidecar-ref.v1",
        "kind": "provider_transcript",
        "ref": "transcripts/missing.jsonl",
        "sha256": "a" * 64,
        "retention": {"class": "audit_required"},
    }
    _write_event(state_dir / "events.jsonl", ZfEvent(
        type="run.started",
        id="evt-run",
        payload={"run_id": "run-1", "transcript_ref": missing_descriptor},
    ))

    report = build_retention_inventory(state_dir)

    assert report["status"] == "degraded"
    assert _item_by_path(report, "workdirs/dev-old/**")["status"] == "blocked"
    assert any(
        issue["code"] == "dangling_sidecar_ref"
        and issue["path"] == "transcripts/missing.jsonl"
        for issue in report["issues"]
    )
    assert all(
        candidate["path"] != "workdirs/dev-old/**"
        for candidate in report["candidates"]
    )


def test_invalid_event_line_blocks_all_reclaim_candidates(tmp_path: Path) -> None:
    state_dir = tmp_path / ".zf"
    (state_dir / "projections").mkdir(parents=True)
    (state_dir / "projections" / "old.json").write_text("{}\n", encoding="utf-8")
    (state_dir / "events.jsonl").write_text("{not-json\n", encoding="utf-8")

    report = build_retention_inventory(state_dir)

    assert report["truth_reference_scan_complete"] is False
    assert report["totals"]["estimated_reclaim_bytes"] == 0
    projection = _item_by_path(report, "projections/old.json")
    assert projection["status"] == "blocked"
    assert projection["reason"] == "truth_reference_scan_incomplete"


def test_retention_plan_cli_is_stable_and_does_not_touch_state(
    tmp_path: Path,
    capsys,
) -> None:
    state_dir = tmp_path / ".zf"
    (state_dir / "projections").mkdir(parents=True)
    (state_dir / "events.jsonl").write_text("", encoding="utf-8")
    (state_dir / "projections" / "old.json").write_text("{}\n", encoding="utf-8")
    args = argparse.Namespace(state_dir=str(state_dir), json=True)

    before = _fingerprints(state_dir)
    first_rc = state_cli._run_retention_plan(args)
    first = capsys.readouterr().out
    middle = _fingerprints(state_dir)
    second_rc = state_cli._run_retention_plan(args)
    second = capsys.readouterr().out
    after = _fingerprints(state_dir)

    assert first_rc == second_rc == 0
    assert first == second
    assert json.loads(first)["delete_supported"] is False
    assert before == middle == after


def _fingerprints(root: Path) -> dict[str, tuple[int, int, str]]:
    return {
        path.relative_to(root).as_posix(): (
            path.stat().st_mtime_ns,
            path.stat().st_size,
            hashlib.sha256(path.read_bytes()).hexdigest(),
        )
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }
