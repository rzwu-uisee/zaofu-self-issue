from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest
import yaml

from zf.core.config.loader import ConfigError, load_config
from zf.core.config.schema import (
    ExecutionProfileConfig,
    ExecutionProfileLimitsConfig,
)
from zf.core.task.schema import Task, TaskContract
from zf.core.task.store import TaskStore
from zf.runtime.backend import BackendCapabilities, get_adapter
from zf.runtime.execution_profiles import (
    DIRECT_PROFILE_ID,
    ExecutionProfileAdmissionError,
    profile_digest,
    resolve_execution_profile,
)
from zf.runtime.run_contract import build_run_contract


def _write_config(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "zf.yaml"
    path.write_text(body, encoding="utf-8")
    return path


def _profile_config(tmp_path: Path, *, policy: str = "fallback_direct"):
    path = _write_config(
        tmp_path,
        f"""\
version: "1.0"
project: {{name: demo}}
workflow:
  execution_profiles:
    adaptive-read-v1:
      strategy: provider_native
      continuation: goal
      collaboration: adaptive
      access: read_only
      capability_policy: {policy}
      limits:
        max_children: 4
        max_depth: 1
        timeout_seconds: 900
        token_budget: 1000
        cost_budget_usd: 2
roles:
  - name: verify
    backend: mock
    role_kind: reader
    execution:
      command: cat
      default_profile: direct-v1
      profile_allowlist: [direct-v1, adaptive-read-v1]
""",
    )
    return path, load_config(path)


def test_legacy_role_normalizes_to_direct_without_command_change(
    tmp_path: Path,
) -> None:
    path = _write_config(
        tmp_path,
        """\
version: "1.0"
project: {name: demo}
roles:
  - name: verify
    backend: mock
    role_kind: reader
""",
    )
    config = load_config(path)
    role = config.roles[0]

    assert role.execution.default_profile == DIRECT_PROFILE_ID
    assert role.execution.profile_allowlist == [DIRECT_PROFILE_ID]
    assert resolve_execution_profile(
        config,
        role_instance=role.instance_id,
    ).shadow_verdict == "supported"
    assert get_adapter(role.backend).build_command(role) == ["cat"]


def test_legal_profile_round_trips_and_run_contract_pins_catalog(
    tmp_path: Path,
) -> None:
    path, config = _profile_config(tmp_path)
    profile = config.workflow.execution_profiles["adaptive-read-v1"]
    assert profile.limits.max_children == 4

    contract = build_run_contract(
        config,
        config_path=path,
        project_root=tmp_path,
        state_dir=tmp_path / ".zf",
    )
    catalog = contract["protocols"]["execution_profile"]
    assert catalog["profiles"]["adaptive-read-v1"]["digest"] == profile_digest(
        profile
    )
    assert catalog["roles"]["verify"]["default_profile"] == DIRECT_PROFILE_ID


@pytest.mark.parametrize(
    "profile",
    [
        {"strategy": "unknown"},
        {"strategy": "provider_native", "access": "writer"},
        {
            "strategy": "direct",
            "limits": {"max_children": 1},
        },
        {
            "strategy": "provider_native",
            "collaboration": "adaptive",
            "limits": {"max_children": 1, "max_depth": 0},
        },
        {
            "strategy": "provider_native",
            "limits": {"cost_budget_usd": -1},
        },
        {
            "strategy": "provider_native",
            "limits": {"token_budget": 10_000_001},
        },
    ],
)
def test_invalid_profile_fails_closed(
    tmp_path: Path,
    profile: dict,
) -> None:
    path = _write_config(
        tmp_path,
        yaml.safe_dump({
            "version": "1.0",
            "project": {"name": "demo"},
            "workflow": {"execution_profiles": {"bad-v1": profile}},
        }),
    )
    with pytest.raises(ConfigError, match="execution_profiles.bad-v1"):
        load_config(path)


def test_role_profile_allowlist_and_pinned_digest_are_admitted(
    tmp_path: Path,
) -> None:
    _, config = _profile_config(tmp_path)
    profile = config.workflow.execution_profiles["adaptive-read-v1"]
    digest = profile_digest(profile)
    resolved = resolve_execution_profile(
        config,
        role_instance="verify",
        contract=TaskContract(
            execution_profile_id="adaptive-read-v1",
            execution_profile_digest=digest,
        ),
    )
    assert resolved.profile_digest == digest
    assert resolved.shadow_verdict == "fallback"
    assert "native_goal" in resolved.shadow_reason

    with pytest.raises(ExecutionProfileAdmissionError, match="not allowed"):
        resolve_execution_profile(
            config,
            role_instance="verify",
            contract={"execution_profile_id": "not-allowed-v1"},
        )


def test_same_name_profile_change_cannot_satisfy_pinned_attempt(
    tmp_path: Path,
) -> None:
    _, config = _profile_config(tmp_path)
    original = config.workflow.execution_profiles["adaptive-read-v1"]
    pinned = profile_digest(original)
    config.workflow.execution_profiles["adaptive-read-v1"] = replace(
        original,
        limits=replace(original.limits, timeout_seconds=1200),
    )

    with pytest.raises(
        ExecutionProfileAdmissionError,
        match="digest mismatch",
    ):
        resolve_execution_profile(
            config,
            role_instance="verify",
            contract={
                "execution_profile_id": "adaptive-read-v1",
                "execution_profile_digest": pinned,
            },
        )


def test_shadow_supported_never_changes_dispatch_route(tmp_path: Path) -> None:
    _, config = _profile_config(tmp_path, policy="require")
    capabilities = BackendCapabilities(
        per_turn_hook=False,
        session_start_hook=False,
        native_resume=True,
        context_usage_reader=True,
        stream_json=True,
        hook_review_required=False,
        nested_agent_disable="full",
        native_goal=True,
        native_multi_agent=True,
        child_lineage="full",
        child_permission_isolation="enforced",
        compound_resume="tree",
        root_only_result_channel=True,
    )
    resolved = resolve_execution_profile(
        config,
        role_instance="verify",
        contract={"execution_profile_id": "adaptive-read-v1"},
        capabilities=capabilities,
    )

    assert resolved.shadow_verdict == "supported"
    assert resolved.projection()["shadow"]["dispatch_effect"] == "none"
    assert get_adapter("mock").build_command(config.roles[0]) == ["cat"]


def test_task_contract_profile_fields_round_trip(tmp_path: Path) -> None:
    store = TaskStore(tmp_path / "kanban.json")
    stored = store.add(Task(
        id="TASK-PROFILE",
        title="profile",
        contract=TaskContract(
            execution_profile_id="direct-v1",
            execution_profile_digest="a" * 64,
        ),
    ))

    assert stored.contract.execution_profile_id == "direct-v1"
    assert store.get("TASK-PROFILE").contract.execution_profile_digest == "a" * 64
