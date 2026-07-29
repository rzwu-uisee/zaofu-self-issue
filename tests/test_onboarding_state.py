"""First-run welcome onboarding gate — global flag, resume, suppress."""

from __future__ import annotations

from pathlib import Path

import pytest

from zf.core.workspace.onboarding import (
    apply_action,
    detect_backends,
    mixed_backends_available,
    onboarding_path,
    read_onboarding,
)


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ZF_WORKSPACE_HOME", str(tmp_path / "home"))


def test_fresh_install_shows_welcome() -> None:
    state = read_onboarding()
    assert state.show_welcome is True
    assert state.step == 1
    assert state.completed is False


def test_complete_suppresses_permanently() -> None:
    apply_action(
        "complete",
        backend="claude-code",
        mixed_enabled=True,
        now="2026-07-07T00:00:00+00:00",
    )
    state = read_onboarding()
    assert state.completed is True
    assert state.show_welcome is False
    assert state.backend == "claude-code"
    assert state.mixed_enabled is True
    assert state.completed_at == "2026-07-07T00:00:00+00:00"


def test_skip_suppresses_permanently() -> None:
    apply_action("skip")
    assert read_onboarding().show_welcome is False


def test_step_persists_for_resume() -> None:
    apply_action("step", step=3, backend="codex")
    state = read_onboarding()
    assert state.step == 3
    assert state.backend == "codex"
    assert state.show_welcome is True  # mid-wizard still shows


def test_reset_re_arms_wizard() -> None:
    apply_action("complete", now="t")
    assert read_onboarding().show_welcome is False
    apply_action("reset")
    state = read_onboarding()
    assert state.show_welcome is True
    assert state.step == 1


def test_invalid_action_rejected() -> None:
    with pytest.raises(ValueError):
        apply_action("bogus")


def test_detect_backends_no_mock_and_mixed_gated() -> None:
    backends = {b["id"]: b for b in detect_backends()}
    # mock 已从欢迎向导后端目录移除(不再作为 onboarding 选项)。
    assert "mock" not in backends
    assert "claude-code" in backends and "codex" in backends
    assert "mixed" not in backends
    both = backends["claude-code"]["detected"] and backends["codex"]["detected"]
    assert mixed_backends_available(list(backends.values())) is both


def test_legacy_mixed_state_migrates_to_primary_plus_policy() -> None:
    path = onboarding_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        '{"schema_version":"onboarding.v1","backend":"mixed","step":2}',
        encoding="utf-8",
    )

    state = read_onboarding()

    assert state.schema_version == "onboarding.v2"
    assert state.backend == "codex"
    assert state.mixed_enabled is True
