from pathlib import Path

from zf.runtime.workflow_refactor_preflight import (
    refactor_safety_report,
    resolve_declared_root,
)


def test_refactor_safety_rejects_overlapping_source_and_target(
    tmp_path: Path,
) -> None:
    report = refactor_safety_report(
        project_root=tmp_path,
        metadata={"source_root": ".", "target_root": "."},
        configured_metadata={"source_root": ".", "target_root": "."},
        flow_kind="refactor",
        intake_report={},
    )

    assert report["status"] == "STOP"
    assert any(
        item["kind"] == "workflow_source_target_overlap"
        for item in report["diagnostics"]
    )
    assert resolve_declared_root("candidate", tmp_path) == tmp_path / "candidate"


def test_refactor_safety_is_not_applicable_to_other_routes(tmp_path: Path) -> None:
    assert refactor_safety_report(
        project_root=tmp_path,
        metadata={},
        configured_metadata={},
        flow_kind="issue",
        intake_report={},
    ) == {"status": "not_applicable", "diagnostics": []}
