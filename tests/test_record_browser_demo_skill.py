from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

from zf.core.config.schema import (
    ProjectConfig,
    RoleConfig,
    SkillSourceConfig,
    ZfConfig,
)
from zf.core.skills.provenance import build_skill_lock_entries, read_skill_metadata


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "skills/zf-record-browser-demo/scripts/encode_gif.py"


@pytest.fixture
def encoder():
    name = "zf_record_browser_demo_encoder"
    spec = importlib.util.spec_from_file_location(name, SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    try:
        yield module
    finally:
        sys.modules.pop(name, None)


def test_encoder_rejects_invalid_durations_and_missing_media_binary(
    encoder,
    monkeypatch,
):
    assert encoder.parse_durations("1.5", 3) == [1.5, 1.5, 1.5]
    with pytest.raises(SystemExit, match="supplied 2 values for 3 frames"):
        encoder.parse_durations("1,2", 3)

    monkeypatch.setattr(encoder.shutil, "which", lambda _name: None)
    with pytest.raises(SystemExit, match="required binary 'ffmpeg'"):
        encoder.require_binary("ffmpeg")


def test_encoder_mock_backend_produces_verified_summary(
    encoder,
    tmp_path,
    monkeypatch,
    capsys,
):
    frame_dir = tmp_path / "frames"
    frame_dir.mkdir()
    for name in ("00-initial.png", "01-settled.png"):
        (frame_dir / name).write_bytes(b"mock-png")
    output = tmp_path / "demo.gif"

    monkeypatch.setattr(encoder, "require_binary", lambda name: f"/mock/{name}")

    def _probe(_ffprobe, path):
        if path == output.resolve():
            return encoder.MediaInfo(800, 450, frame_count=10, duration_seconds=2.0)
        return encoder.MediaInfo(800, 450)

    monkeypatch.setattr(encoder, "probe_media", _probe)
    calls = []

    def _run(command, **kwargs):
        calls.append((command, kwargs))
        Path(command[-1]).write_bytes(b"GIF89a-mock")
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(encoder.subprocess, "run", _run)

    encoder.main([
        str(frame_dir),
        str(output),
        "--durations",
        "1,1",
        "--fps",
        "5",
        "--max-width",
        "800",
    ])

    summary = json.loads(capsys.readouterr().out)
    assert summary["schema_version"] == "browser-demo-encoder-summary.v1"
    assert summary["source_frames"] == 2
    assert summary["encoded_frames"] == 10
    assert summary["duration_seconds"] == 2.0
    assert summary["output"] == str(output.resolve())
    command, kwargs = calls[0]
    assert command[0] == "/mock/ffmpeg"
    assert "palettegen=max_colors=128" in command[command.index("-vf") + 1]
    assert kwargs["timeout"] == 120


def test_new_skills_are_on_demand_and_keep_evidence_boundaries():
    simplify_path = ROOT / "skills/zf-find-simplifications/SKILL.md"
    browser_path = ROOT / "skills/zf-record-browser-demo/SKILL.md"
    simplify = simplify_path.read_text(encoding="utf-8")
    browser = browser_path.read_text(encoding="utf-8")
    simplify_metadata = read_skill_metadata(
        simplify_path,
        expected_name="zf-find-simplifications",
    )
    browser_metadata = read_skill_metadata(
        browser_path,
        expected_name="zf-record-browser-demo",
    )

    assert simplify_metadata.load_on_demand is True
    assert simplify_metadata.auto_inject is False
    assert simplify_metadata.dependencies == ()
    assert "applications developed with ZaoFu" in simplify
    assert "simplification_audit.v1" in simplify
    assert "apply_policy: proposal_only" in simplify
    assert "Recent-Diff Refinement" in simplify
    assert "Fewer lines are not a" in simplify
    assert browser_metadata.load_on_demand is True
    assert browser_metadata.dependencies == (
        "zf-browser-e2e-contract",
        "zf-harness-evidence-collection",
    )
    assert "A GIF is never a" in browser
    assert "provider_mode" in browser


@pytest.mark.parametrize(
    ("skill", "dependencies"),
    [
        (
            "zf-find-simplifications",
            set(),
        ),
        (
            "zf-record-browser-demo",
            {"zf-browser-e2e-contract", "zf-harness-evidence-collection"},
        ),
    ],
)
def test_new_skill_materializes_with_dependencies_for_external_app(
    tmp_path,
    skill,
    dependencies,
):
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    role = RoleConfig(name="dev", backend="mock", skills=[skill])
    config = ZfConfig(
        project=ProjectConfig(name="external-app"),
        roles=[role],
        skill_sources=[
            SkillSourceConfig(
                name="zaofu",
                path=str(ROOT / "skills"),
                mode="readonly",
            )
        ],
    )

    entries = build_skill_lock_entries(
        project_root=tmp_path,
        state_dir=state_dir,
        role=role,
        config=config,
    )
    by_name = {entry.name: entry for entry in entries}

    assert by_name[skill].status == "resolved"
    assert by_name[skill].load_on_demand is True
    assert dependencies.issubset(by_name)
    assert all(by_name[name].status == "resolved" for name in dependencies)


@pytest.mark.parametrize(
    "entry_skill",
    ["zf-harness-self-improve", "zf-yoke-dev-worker-role-context"],
)
def test_simplifier_is_available_on_demand_from_runtime_entry_skills(
    tmp_path,
    entry_skill,
):
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    role = RoleConfig(
        name="dev",
        backend="mock",
        stages=["impl"],
        skills=[entry_skill],
    )
    config = ZfConfig(
        project=ProjectConfig(name="external-app"),
        roles=[role],
        skill_sources=[
            SkillSourceConfig(
                name="zaofu",
                path=str(ROOT / "skills"),
                mode="readonly",
            )
        ],
    )

    entries = build_skill_lock_entries(
        project_root=tmp_path,
        state_dir=state_dir,
        role=role,
        config=config,
    )
    simplifier = {entry.name: entry for entry in entries}[
        "zf-find-simplifications"
    ]

    assert simplifier.status == "resolved"
    assert simplifier.auto_inject is False
    assert simplifier.load_on_demand is True
    assert simplifier.dependency_of == (entry_skill,)
    assert simplifier.routing_warnings == ()


def test_provider_skill_distributions_match_canonical_resources():
    for provider_root in (ROOT / ".claude/skills", ROOT / ".codex/skills"):
        for skill, relative_paths in {
            "zf-find-simplifications": ["SKILL.md"],
            "zf-record-browser-demo": [
                "SKILL.md",
                "THIRD_PARTY_LICENSE.md",
                "scripts/encode_gif.py",
            ],
        }.items():
            for relative_path in relative_paths:
                canonical = ROOT / "skills" / skill / relative_path
                distributed = provider_root / skill / relative_path
                assert distributed.read_bytes() == canonical.read_bytes()
