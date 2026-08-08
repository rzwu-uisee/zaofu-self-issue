from __future__ import annotations

import json
import subprocess
from types import SimpleNamespace

import pytest

from zf.runtime.self_repair_process import LAUNCH_SCHEMA, run_launch


def _launch(tmp_path, *, timeout_seconds=30):
    launch = tmp_path / "launch.json"
    launch.write_text(json.dumps({
        "schema_version": LAUNCH_SCHEMA,
        "operation_id": "self-repair-op-1",
        "backend": "mock",
        "argv": ["mock-provider", "--run"],
        "cwd": str(tmp_path),
        "timeout_seconds": timeout_seconds,
        "repair_contract_digest": "a" * 64,
    }), encoding="utf-8")
    return launch, tmp_path / "result.json"


@pytest.mark.parametrize(
    ("returncode", "expected_status"),
    [(0, "completed"), (7, "failed")],
)
def test_process_wrapper_persists_exit_status(
    tmp_path,
    monkeypatch,
    returncode,
    expected_status,
):
    launch, result_path = _launch(tmp_path)
    monkeypatch.setattr(
        "zf.runtime.self_repair_process.subprocess.run",
        lambda *args, **kwargs: SimpleNamespace(returncode=returncode),
    )

    result = run_launch(launch, result_path)

    assert result["status"] == expected_status
    assert result["returncode"] == returncode
    assert json.loads(result_path.read_text(encoding="utf-8")) == result


def test_process_wrapper_persists_timeout(tmp_path, monkeypatch):
    launch, result_path = _launch(tmp_path, timeout_seconds=1)

    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(args[0], timeout=1)

    monkeypatch.setattr(
        "zf.runtime.self_repair_process.subprocess.run",
        timeout,
    )

    result = run_launch(launch, result_path)

    assert result["status"] == "timeout"
    assert result["returncode"] == 124
    assert result["reason"] == "provider_timeout"
