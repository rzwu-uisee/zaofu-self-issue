from zf.core.events.model import ZfEvent
from zf.runtime.workflow_read_result import normalize_workflow_read_result


def test_artifact_production_findings_are_output_not_execution_failure() -> None:
    result, issues = normalize_workflow_read_result(ZfEvent(
        type="workflow.child.failed",
        payload={
            "result_semantics": "artifact_production",
            "summary": "Scan completed with blocking findings.",
            "findings": [{"severity": "high", "message": "gap"}],
            "recommendation": "needs_rework",
        },
    ))

    assert issues == []
    assert result["execution_status"] == "completed"
    assert result["verdict"] == "passed"
    assert result["subject_verdict"] == "needs_rework"


def test_artifact_production_preserves_explicit_transport_failure() -> None:
    result, issues = normalize_workflow_read_result(ZfEvent(
        type="workflow.child.failed",
        payload={
            "result_semantics": "artifact_production",
            "summary": "Provider stream stopped.",
            "transport_error": "broken pipe",
        },
    ))

    assert issues == []
    assert result["execution_status"] == "failed"
    assert result["verdict"] == "abstained"
    assert result["failure_class"] == "reader_execution_failure"
