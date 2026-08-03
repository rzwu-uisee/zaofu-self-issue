from zf.runtime.fanout_result_identity import (
    bind_blocking_writer_result_identity,
)


def test_blocking_writer_result_uses_kernel_identity() -> None:
    canonical_ref = (
        "artifacts/task-contract-snapshots/Run-UPPER/"
        "TASK-1/contract-r1.json"
    )
    result = bind_blocking_writer_result_identity(
        {
            "source_commit": "abc123",
            "fanout_id": "fanout-old",
            "stage_id": "stage-old",
            "child_id": "child-old",
            "run_id": "run-old",
            "contract_snapshot_ref": canonical_ref.lower(),
            "semantic_summary": "implemented",
            "impl_self_check": {
                "contract_snapshot_ref": canonical_ref.lower(),
                "evidence_refs": ["event:verified"],
            },
        },
        {
            "task_id": "TASK-1",
            "fanout_id": "fanout-current",
            "stage_id": "stage-current",
            "child_id": "child-current",
            "run_id": "run-current",
            "attempt_id": "attempt-1",
            "workflow_run_id": "Run-UPPER",
            "contract_revision": "contract-r1",
            "task_map_generation": "generation-1",
            "contract_snapshot_ref": canonical_ref,
            "contract_snapshot_digest": "digest-1",
            "result_protocol_mode": "blocking",
        },
    )

    assert result["contract_snapshot_ref"] == canonical_ref
    assert result["fanout_id"] == "fanout-current"
    assert result["stage_id"] == "stage-current"
    assert result["child_id"] == "child-current"
    assert result["run_id"] == "run-current"
    assert result["workflow_run_id"] == "Run-UPPER"
    assert result["semantic_summary"] == "implemented"
    self_check = result["impl_self_check"]
    assert self_check["contract_snapshot_ref"] == canonical_ref
    assert self_check["source_commit"] == "abc123"
    assert self_check["target_commit"] == "abc123"
    assert self_check["evidence_refs"] == ["event:verified"]


def test_legacy_writer_result_keeps_reported_identity() -> None:
    result = bind_blocking_writer_result_identity(
        {"contract_snapshot_ref": "reported"},
        {
            "contract_snapshot_ref": "canonical",
            "result_protocol_mode": "legacy",
        },
    )

    assert result["contract_snapshot_ref"] == "reported"
