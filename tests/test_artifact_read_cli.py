from __future__ import annotations

import hashlib
import json
from pathlib import Path

from zf.cli.main import main
from zf.runtime.artifact_read_ledger import (
    build_attempt_source_manifest,
    write_attempt_source_manifest,
)
from zf.runtime.artifact_read_capability import (
    bind_attempt_artifact_read_capability,
    provision_role_artifact_read_credential,
)


def test_artifact_list_and_read_cli_record_attempt_ledger(
    tmp_path: Path,
    capsys,
    monkeypatch,
) -> None:
    state_dir = tmp_path / ".zf"
    artifact = state_dir / "artifacts" / "inputs" / "facts.json"
    artifact.parent.mkdir(parents=True)
    artifact.write_text(json.dumps({"facts": ["one", "two"]}), encoding="utf-8")
    digest = hashlib.sha256(artifact.read_bytes()).hexdigest()
    manifest = build_attempt_source_manifest(
        workflow_run_id="run-cli",
        task_id="T-CLI",
        attempt_id="attempt-cli",
        dispatch_id="attempt-cli",
        sources=[{
            "source_id": "context",
            "artifact_id": "facts",
            "ref": "artifacts/inputs/facts.json",
            "sha256": digest,
            "allowed_paths": ["$.facts"],
        }],
    )
    write_attempt_source_manifest(state_dir, manifest)
    token_path = provision_role_artifact_read_credential(
        state_dir,
        "dev-1",
        role_name="dev",
        provider="codex",
    )
    bind_attempt_artifact_read_capability(
        state_dir,
        operation_id="",
        attempt_id="attempt-cli",
        role_instance="dev-1",
        manifest=manifest,
    )
    for key in (
        "ZF_ROLE_INSTANCE",
        "ZF_ROLE_NAME",
        "ZF_ROLE_BACKEND",
        "ZF_ARTIFACT_READ_TOKEN_FILE",
    ):
        monkeypatch.delenv(key, raising=False)

    assert main([
        "artifact", "list", "--attempt", "attempt-cli",
        "--state-dir", str(state_dir),
    ]) == 0
    listed = json.loads(capsys.readouterr().out)
    assert listed["sources"][0]["artifact_id"] == "facts"

    monkeypatch.setenv("ZF_ROLE_INSTANCE", "dev-1")
    monkeypatch.setenv("ZF_ROLE_NAME", "dev")
    monkeypatch.setenv("ZF_ROLE_BACKEND", "codex")
    monkeypatch.setenv("ZF_ARTIFACT_READ_TOKEN_FILE", str(token_path))

    assert main([
        "artifact", "read", "--attempt", "attempt-cli",
        "--source", "context", "--artifact", "facts",
        "--json-path", "$.facts", "--state-dir", str(state_dir),
    ]) == 0
    assert '"one"' in capsys.readouterr().out
    ledger = (
        state_dir / "artifacts/attempts/attempt-cli/read-ledger.active.jsonl"
    )
    assert ledger.exists()

    assert main([
        "artifact", "read", "--attempt", "attempt-cli",
        "--source", "context", "--artifact", "facts",
        "--json-path", "$.facts", "--state-dir", str(state_dir),
    ]) == 0
    capsys.readouterr()
    row = json.loads(ledger.read_text(encoding="utf-8").splitlines()[-1])
    assert row["consumer_actor"] == "dev-1"
    assert row["consumer_role"] == "dev"
    assert row["consumer_provider"] == "codex"


def test_artifact_read_cli_rejects_forged_role_identity_before_io(
    tmp_path: Path,
    capsys,
    monkeypatch,
) -> None:
    state_dir = tmp_path / ".zf"
    artifact = state_dir / "artifacts" / "inputs" / "restricted.json"
    artifact.parent.mkdir(parents=True)
    artifact.write_text(json.dumps({"secret": "value"}), encoding="utf-8")
    manifest = build_attempt_source_manifest(
        workflow_run_id="run-auth",
        task_id="T-AUTH",
        attempt_id="attempt-auth",
        dispatch_id="attempt-auth",
        metadata={"read_purpose": "implementation"},
        sources=[{
            "source_id": "contract",
            "artifact_id": "restricted",
            "ref": "artifacts/inputs/restricted.json",
            "sha256": hashlib.sha256(artifact.read_bytes()).hexdigest(),
            "access_scope": {
                "visibility": "project",
                "roles": ["dev"],
                "purposes": ["implementation"],
            },
        }],
    )
    write_attempt_source_manifest(state_dir, manifest)
    token_path = provision_role_artifact_read_credential(
        state_dir,
        "verify-1",
        role_name="verify",
        provider="codex",
    )
    bind_attempt_artifact_read_capability(
        state_dir,
        operation_id="",
        attempt_id="attempt-auth",
        role_instance="verify-1",
        manifest=manifest,
    )
    monkeypatch.setenv("ZF_ROLE_INSTANCE", "verify-1")
    monkeypatch.setenv("ZF_ROLE_NAME", "dev")
    monkeypatch.setenv("ZF_ROLE_BACKEND", "codex")
    monkeypatch.setenv("ZF_ARTIFACT_PURPOSE", "implementation")
    monkeypatch.setenv("ZF_ARTIFACT_READ_TOKEN_FILE", str(token_path))

    assert main([
        "artifact", "read", "--attempt", "attempt-auth",
        "--source", "contract", "--artifact", "restricted",
        "--state-dir", str(state_dir),
    ]) == 1
    assert "does not match the issued capability" in capsys.readouterr().err
    ledger = (
        state_dir / "artifacts/attempts/attempt-auth/read-ledger.active.jsonl"
    )
    assert not ledger.exists()
