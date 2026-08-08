from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys

from tests.e2e.five_workflow_runner_support import (
    capture_screenshot,
    collect_refs,
    command_result,
    git_snapshot,
    read_json,
    write_json,
)


def test_support_collects_nested_refs_and_writes_atomic_json(tmp_path: Path) -> None:
    refs = collect_refs([{
        "artifact_refs": [{
            "kind": "report",
            "ref": "artifacts/report.md",
            "sha256": "a" * 64,
        }],
        "manifest_ref": "workflow-inputs/RUN-1/manifest.json",
    }])
    out = write_json(tmp_path / "refs.json", {"refs": refs})

    assert read_json(out) == {"refs": refs}
    assert [item["ref"] for item in refs] == [
        "artifacts/report.md",
        "workflow-inputs/RUN-1/manifest.json",
    ]
    assert not (tmp_path / "refs.json.tmp").exists()


def test_support_command_and_screenshot_capture_use_argv_without_shell(
    tmp_path: Path,
) -> None:
    screenshot = tmp_path / "failure.png"
    result = command_result([
        sys.executable,
        "-c",
        "print('runner-ready')",
    ])
    capture_screenshot([
        sys.executable,
        "-c",
        f"from pathlib import Path; Path({str(screenshot)!r}).write_bytes(b'png')",
    ], out_dir=tmp_path)

    assert result["returncode"] == 0
    assert result["stdout"].strip() == "runner-ready"
    assert screenshot.read_bytes() == b"png"
    receipt = json.loads((tmp_path / "screenshot-command.json").read_text())
    assert receipt["status"] == "passed"
    assert receipt["argv"][0] == sys.executable


def test_support_git_snapshot_detects_clean_and_dirty_seed(tmp_path: Path) -> None:
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    subprocess.run(
        ["git", "-C", str(tmp_path), "config", "user.name", "ZaoFu E2E"],
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(tmp_path), "config", "user.email", "e2e@example.test"],
        check=True,
    )
    seed = tmp_path / "seed.txt"
    seed.write_text("seed\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(tmp_path), "add", "seed.txt"], check=True)
    subprocess.run(
        ["git", "-C", str(tmp_path), "commit", "-qm", "test: seed"],
        check=True,
    )

    clean = git_snapshot(tmp_path)
    seed.write_text("changed\n", encoding="utf-8")
    dirty = git_snapshot(tmp_path)

    assert clean["dirty"] is False
    assert len(clean["head"]) == 40
    assert dirty["dirty"] is True
    assert dirty["status"] == [" M seed.txt"]
