"""Redacted environment identity and admission checks for evolution trials.

The evolution evaluator must not treat two provider runs as comparable merely
because they share a prompt.  This module captures the deterministic host and
toolchain facts that materially affect an isolated trial, then checks those
facts again immediately before a provider call.  It deliberately never
installs dependencies, changes credentials, or probes a paid model endpoint.
"""

from __future__ import annotations

import hashlib
import os
import platform
import shutil
import subprocess
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from zf.runtime.call_result_envelope import write_immutable_json_sidecar
from zf.runtime.evolution_contracts import EvolutionContractError, stable_digest
from zf.runtime.preflight import probe_provider_auth


CommandRunner = Callable[..., subprocess.CompletedProcess[str]]
ProviderAuthProbe = Callable[[str], tuple[bool, str]]
Which = Callable[[str], str | None]
EnvironmentSnapshotter = Callable[..., Mapping[str, Any]]

ENVIRONMENT_SNAPSHOT_SCHEMA = "evolution-environment-capability.v1"
ENVIRONMENT_PREFLIGHT_SCHEMA = "evolution-environment-preflight.v1"

_PROVIDER_COMMANDS = {
    "codex": "codex",
    "claude-code": "claude",
}
_LOCKFILE_NAMES = (
    "pyproject.toml",
    "uv.lock",
    "package.json",
    "package-lock.json",
    "pnpm-lock.yaml",
    "yarn.lock",
)
_FROZEN_DIGEST_FIELDS = (
    "provider_capability_digest",
    "toolchain_digest",
    "environment_digest",
    "sandbox_policy_digest",
    "network_policy_digest",
    "credential_policy_digest",
)


def capture_evolution_environment(
    *,
    project_root: Path,
    state_dir: Path,
    backend: str,
    model: str,
    reasoning_effort: str = "",
    token_env: str,
    sealed_root: str,
    command_runner: CommandRunner = subprocess.run,
    which: Which = shutil.which,
    auth_probe: ProviderAuthProbe = probe_provider_auth,
    environ: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Capture stable, redacted facts needed to compare an evolution trial.

    The returned body intentionally excludes full environment variables,
    credential values, filesystem paths outside the project, and command
    output beyond a provider/tool version first line.
    """

    root = Path(project_root).resolve(strict=False)
    state = Path(state_dir).resolve(strict=False)
    backend = str(backend or "").strip()
    source_env = os.environ if environ is None else environ
    sealed_path = Path(sealed_root).expanduser() if sealed_root else Path()
    if sealed_root and not sealed_path.is_absolute():
        sealed_path = root / sealed_path
    command = _PROVIDER_COMMANDS.get(backend, backend)
    cli_path = str(which(command) or "") if command else ""
    cli_version = _version_probe(command, command_runner=command_runner, which=which)
    auth_ready = False
    if cli_path and backend in _PROVIDER_COMMANDS:
        auth_ready, _ = auth_probe(backend)

    token = str(source_env.get(token_env) or "")
    return {
        "schema_version": ENVIRONMENT_SNAPSHOT_SCHEMA,
        "provider": {
            "backend": backend,
            "model": str(model or "provider-default"),
            "model_reasoning_effort": str(reasoning_effort or ""),
            "command": command,
            "cli": {
                "available": bool(cli_path),
                "version_ok": bool(cli_version["ok"]),
                "version": str(cli_version["version"]),
            },
            "auth": {
                "checked": bool(cli_path and backend in _PROVIDER_COMMANDS),
                "ready": bool(auth_ready),
            },
        },
        "toolchain": {
            "python": {
                "implementation": platform.python_implementation(),
                "version": platform.python_version(),
            },
            "uv": _version_probe("uv", command_runner=command_runner, which=which),
            "node": _version_probe("node", command_runner=command_runner, which=which),
            "lockfiles": {
                name: _file_digest(root / name)
                for name in _LOCKFILE_NAMES
            },
        },
        "environment": {
            "host": {
                "system": platform.system(),
                "release": platform.release(),
                "machine": platform.machine(),
            },
            "project": {
                "name": root.name,
                "git": _git_identity(root, command_runner=command_runner),
            },
            "state_dir": {
                "exists": state.exists(),
                "writable": os.access(state, os.W_OK) if state.exists() else False,
            },
            "sealed_evaluator": {
                "configured": bool(str(sealed_root or "").strip()),
                "directory_exists": sealed_path.is_dir() if sealed_root else False,
            },
        },
        "sandbox": _sandbox_policy(backend),
        "network": {
            "policy": "provider_managed",
            "reachability": "not_probed_without_provider_turn",
        },
        "credential_policy": {
            "evaluator_token_env": str(token_env or ""),
            "evaluator_token_present": bool(token),
            "evaluator_token_minimum_length_met": len(token) >= 16,
            "secret_value_persisted": False,
        },
    }


def environment_identity_digests(snapshot: Mapping[str, Any]) -> dict[str, str]:
    """Return the exact frozen digest fields derived from one snapshot."""

    return {
        "provider_capability_digest": stable_digest(_section(snapshot, "provider")),
        "toolchain_digest": stable_digest(_section(snapshot, "toolchain")),
        "environment_digest": stable_digest(_section(snapshot, "environment")),
        "sandbox_policy_digest": stable_digest(_section(snapshot, "sandbox")),
        "network_policy_digest": stable_digest(_section(snapshot, "network")),
        "credential_policy_digest": stable_digest(_section(snapshot, "credential_policy")),
    }


@dataclass(frozen=True)
class CampaignEnvironmentFacts:
    """One campaign's full capability snapshot and immutable section refs."""

    capability: dict[str, Any]
    capability_snapshot: dict[str, Any]
    provider_snapshot: dict[str, Any]
    toolchain_snapshot: dict[str, Any]
    environment_snapshot: dict[str, Any]
    sandbox_snapshot: dict[str, Any]
    network_snapshot: dict[str, Any]
    credential_snapshot: dict[str, Any]
    digests: dict[str, str]


def freeze_campaign_environment(
    *,
    state_dir: Path,
    project_root: Path,
    source_event_id: str,
    backend: str,
    model: str,
    reasoning_effort: str,
    token_env: str,
    sealed_root: str,
    snapshotter: EnvironmentSnapshotter = capture_evolution_environment,
) -> CampaignEnvironmentFacts:
    """Persist one coherent, redacted environment identity for a campaign."""

    capability = dict(snapshotter(
        project_root=project_root,
        state_dir=state_dir,
        backend=backend,
        model=model,
        reasoning_effort=reasoning_effort,
        token_env=token_env,
        sealed_root=sealed_root,
    ))
    if str(capability.get("schema_version") or "") != ENVIRONMENT_SNAPSHOT_SCHEMA:
        raise EvolutionContractError("evolution environment snapshot schema is invalid")
    digests = environment_identity_digests(capability)
    capability_snapshot = write_immutable_json_sidecar(
        state_dir,
        capability,
        root="evolution/environment-capabilities",
        kind="evolution_environment_capability",
        schema_version=ENVIRONMENT_SNAPSHOT_SCHEMA,
        created_by="run-manager",
        source_event_id=source_event_id,
    )
    if str(capability_snapshot.get("sha256") or "") != stable_digest(capability):
        raise EvolutionContractError("evolution environment capability snapshot digest mismatch")
    provider_snapshot = _campaign_snapshot(
        state_dir, "provider", _required_section(capability, "provider"), source_event_id
    )
    toolchain_snapshot = _campaign_snapshot(
        state_dir, "toolchain", _required_section(capability, "toolchain"), source_event_id
    )
    environment_snapshot = _campaign_snapshot(
        state_dir, "environment", _required_section(capability, "environment"), source_event_id
    )
    sandbox_snapshot = _campaign_snapshot(
        state_dir, "sandbox", _required_section(capability, "sandbox"), source_event_id
    )
    network_snapshot = _campaign_snapshot(
        state_dir, "network", _required_section(capability, "network"), source_event_id
    )
    credential_snapshot = _campaign_snapshot(
        state_dir,
        "credential-policy",
        _required_section(capability, "credential_policy"),
        source_event_id,
    )
    snapshots = {
        "provider_capability_digest": provider_snapshot,
        "toolchain_digest": toolchain_snapshot,
        "environment_digest": environment_snapshot,
        "sandbox_policy_digest": sandbox_snapshot,
        "network_policy_digest": network_snapshot,
        "credential_policy_digest": credential_snapshot,
    }
    mismatches = sorted(
        field
        for field, descriptor in snapshots.items()
        if str(descriptor.get("sha256") or "") != digests[field]
    )
    if mismatches:
        raise EvolutionContractError(
            "evolution environment snapshot digest mismatch: " + ", ".join(mismatches)
        )
    return CampaignEnvironmentFacts(
        capability=capability,
        capability_snapshot=capability_snapshot,
        provider_snapshot=provider_snapshot,
        toolchain_snapshot=toolchain_snapshot,
        environment_snapshot=environment_snapshot,
        sandbox_snapshot=sandbox_snapshot,
        network_snapshot=network_snapshot,
        credential_snapshot=credential_snapshot,
        digests=digests,
    )


def evaluate_evolution_environment(
    snapshot: Mapping[str, Any],
    *,
    frozen_inputs: Mapping[str, Any],
) -> dict[str, Any]:
    """Return a redacted, non-retryable admission verdict for one provider call."""

    provider = _section(snapshot, "provider")
    cli = _section(provider, "cli")
    auth = _section(provider, "auth")
    environment = _section(snapshot, "environment")
    sealed = _section(environment, "sealed_evaluator")
    credential = _section(snapshot, "credential_policy")
    observed = environment_identity_digests(snapshot)
    expected = {
        field: str(frozen_inputs.get(field) or "")
        for field in _FROZEN_DIGEST_FIELDS
    }
    drifted = sorted(
        field for field in _FROZEN_DIGEST_FIELDS
        if not expected[field] or expected[field] != observed[field]
    )
    checks = [
        _check(
            "provider_cli",
            bool(cli.get("available")),
            "provider CLI is available" if cli.get("available") else "provider CLI is unavailable",
            "evolution_environment_provider_cli_missing",
        ),
        _check(
            "provider_cli_version",
            bool(cli.get("version_ok")),
            "provider CLI version was captured" if cli.get("version_ok") else "provider CLI version probe failed",
            "evolution_environment_provider_version_unavailable",
        ),
        _check(
            "provider_auth",
            bool(auth.get("ready")),
            "provider authentication is ready" if auth.get("ready") else "provider authentication is unavailable",
            "evolution_environment_provider_auth_unavailable",
        ),
        _check(
            "evaluator_token",
            bool(credential.get("evaluator_token_minimum_length_met")),
            "evaluator token is present" if credential.get("evaluator_token_minimum_length_met") else "evaluator token is unavailable",
            "evolution_environment_evaluator_token_missing",
        ),
        _check(
            "sealed_evaluator",
            bool(sealed.get("configured")) and bool(sealed.get("directory_exists")),
            "sealed evaluator root is available"
            if bool(sealed.get("configured")) and bool(sealed.get("directory_exists"))
            else "sealed evaluator root is unavailable",
            "evolution_environment_sealed_root_missing",
        ),
        _check(
            "frozen_identity",
            not drifted,
            "observed environment matches the frozen campaign identity"
            if not drifted
            else "observed environment differs from frozen fields: " + ", ".join(drifted),
            "evolution_environment_comparison_drift",
        ),
    ]
    failed = next((check for check in checks if not check["ok"]), None)
    return {
        "schema_version": ENVIRONMENT_PREFLIGHT_SCHEMA,
        "status": "passed" if failed is None else "failed",
        "ok": failed is None,
        "retryable": False,
        "failure_class": "" if failed is None else failed["failure_class"],
        "checks": checks,
        "expected_digests": expected,
        "observed_digests": observed,
        "snapshot_digest": stable_digest(dict(snapshot)),
    }


def environment_archive_env(preflight: Mapping[str, Any]) -> dict[str, str]:
    """Provide only non-secret environment facts to ``RunArchive``."""

    observed = _section(preflight, "observed_digests")
    return {
        "ZF_EVOLUTION_ENVIRONMENT_DIGEST": str(observed.get("environment_digest") or ""),
        "ZF_EVOLUTION_PREFLIGHT_STATUS": str(preflight.get("status") or ""),
        "ZF_EVOLUTION_PREFLIGHT_FAILURE_CLASS": str(preflight.get("failure_class") or ""),
    }


def _version_probe(
    command: str,
    *,
    command_runner: CommandRunner,
    which: Which,
) -> dict[str, Any]:
    path = str(which(command) or "")
    if not path:
        return {"available": False, "ok": False, "version": ""}
    try:
        result = command_runner(
            [path, "--version"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return {"available": True, "ok": False, "version": ""}
    output = "\n".join(
        item.strip() for item in (result.stdout, result.stderr) if item and item.strip()
    )
    return {
        "available": True,
        "ok": result.returncode == 0,
        "version": output.splitlines()[0][:200] if result.returncode == 0 and output else "",
    }


def _git_identity(project_root: Path, *, command_runner: CommandRunner) -> dict[str, Any]:
    try:
        head = command_runner(
            ["git", "rev-parse", "HEAD"],
            cwd=project_root,
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        status = command_runner(
            ["git", "status", "--porcelain", "--untracked-files=no"],
            cwd=project_root,
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return {"available": False, "head": "", "dirty": False, "status_digest": ""}
    if head.returncode != 0 or status.returncode != 0:
        return {"available": False, "head": "", "dirty": False, "status_digest": ""}
    raw_status = str(status.stdout or "")
    return {
        "available": True,
        "head": str(head.stdout or "").strip(),
        "dirty": bool(raw_status.strip()),
        "status_digest": stable_digest(raw_status),
    }


def _file_digest(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {"present": False, "sha256": ""}
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return {"present": True, "sha256": digest.hexdigest()}


def _sandbox_policy(backend: str) -> dict[str, Any]:
    if backend == "codex":
        return {
            "mode": "read_only",
            "workdir": "fresh_temp",
            "user_config": "ignored",
            "session": "ephemeral",
        }
    return {
        "mode": "provider_default",
        "workdir": "fresh_temp",
        "user_config": "host_default",
        "session": "provider_managed",
    }


def _campaign_snapshot(
    state_dir: Path,
    label: str,
    body: Mapping[str, Any],
    source_event_id: str,
) -> dict[str, Any]:
    return write_immutable_json_sidecar(
        state_dir,
        dict(body),
        root=f"evolution/snapshots/{label}",
        kind=f"evolution_{label}_snapshot",
        schema_version=f"evolution-{label}-snapshot.v1",
        created_by="run-manager",
        source_event_id=source_event_id,
    )


def _required_section(snapshot: Mapping[str, Any], key: str) -> dict[str, Any]:
    value = _section(snapshot, key)
    if not value:
        raise EvolutionContractError(
            f"evolution environment snapshot lacks non-empty {key!r} section"
        )
    return value


def _section(value: Mapping[str, Any], key: str) -> dict[str, Any]:
    raw = value.get(key)
    return dict(raw) if isinstance(raw, Mapping) else {}


def _check(name: str, ok: bool, detail: str, failure_class: str) -> dict[str, Any]:
    return {
        "name": name,
        "ok": bool(ok),
        "detail": detail,
        "failure_class": "" if ok else failure_class,
    }


__all__ = [
    "CampaignEnvironmentFacts",
    "CommandRunner",
    "ENVIRONMENT_PREFLIGHT_SCHEMA",
    "ENVIRONMENT_SNAPSHOT_SCHEMA",
    "EnvironmentSnapshotter",
    "ProviderAuthProbe",
    "capture_evolution_environment",
    "environment_archive_env",
    "environment_identity_digests",
    "evaluate_evolution_environment",
    "freeze_campaign_environment",
]
