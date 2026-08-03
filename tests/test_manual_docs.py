from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path
import sys
from types import ModuleType

from zf.cli.main import build_parser


ROOT = Path(__file__).resolve().parents[1]


def _load_manual_docs() -> ModuleType:
    path = ROOT / "scripts" / "manual-docs.py"
    spec = importlib.util.spec_from_file_location("zf_manual_docs", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["zf_manual_docs"] = module
    spec.loader.exec_module(module)
    return module


def _load_script(name: str, module_name: str) -> ModuleType:
    path = ROOT / "scripts" / name
    spec = importlib.util.spec_from_file_location(module_name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def test_generated_cli_inventory_tracks_parser_tree() -> None:
    manual_docs = _load_manual_docs()
    parser = build_parser()
    subparsers = next(
        action
        for action in parser._actions
        if isinstance(action, argparse._SubParsersAction)
    )

    inventory = manual_docs.render_cli_inventory("en")

    assert f"**{len(subparsers.choices)}** top-level families" in inventory
    for command in (
        "zf workflow routes",
        "zf workflow start",
        "zf artifact list",
        "zf artifact read",
        "zf artifact catalog",
        "zf goal show",
        "zf goal set",
        "zf projection doctor",
        "zf report goal-dossier",
    ):
        assert f"`{command}`" in inventory


def test_manual_docs_currentness_gate_is_green() -> None:
    manual_docs = _load_manual_docs()

    assert manual_docs.currentness_errors() == []


def test_user_manual_does_not_depend_on_design_docs() -> None:
    manual_docs = _load_manual_docs()

    assert manual_docs.design_reference_errors() == []
    coverage = manual_docs.load_coverage()
    assert all("design" not in capability for capability in coverage["capabilities"])


def test_manual_cli_examples_and_execution_matrix_track_parser() -> None:
    manual_docs = _load_manual_docs()

    assert manual_docs.command_contract_errors() == []


def test_manual_command_contract_rejects_unknown_cli_option() -> None:
    commands = _load_script("manual_commands.py", "zf_manual_commands_test")

    error, path = commands.validate_command_argv(
        ("status", "--workers", "--state-dir", "/tmp/state"),
        build_parser(),
    )

    assert path == ("zf", "status")
    assert error == "unknown option '--state-dir' for zf status"


def test_manual_smoke_terminal_evidence_is_tolerant_and_payload_specific(
    tmp_path: Path,
) -> None:
    smoke = _load_script("manual-command-smoke.py", "zf_manual_command_smoke")
    events = tmp_path / "events.jsonl"
    events.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "type": "task.status_changed",
                        "id": "evt-old",
                        "task_id": "TASK-1",
                        "payload": {"to": "in_progress"},
                    }
                ),
                "{broken",
                json.dumps(
                    {
                        "type": "task.status_changed",
                        "id": "evt-done",
                        "ts": "2026-08-03T00:00:00+00:00",
                        "task_id": "TASK-1",
                        "payload": {"to": "done"},
                    }
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    evidence, malformed = smoke.terminal_evidence(
        events,
        [
            {
                "event_type": "task.status_changed",
                "task_id": "TASK-1",
                "payload": {"to": "done"},
            }
        ],
    )

    assert malformed == [2]
    assert evidence[0]["found"] is True
    assert evidence[0]["event_id"] == "evt-done"
    assert evidence[0]["line"] == 3


def test_manual_smoke_state_change_scope_is_explicit() -> None:
    smoke = _load_script("manual-command-smoke.py", "zf_manual_command_scope")

    assert smoke._unexpected_state_changes(
        "completed-project-projection-refresh",
        ["projections/read_model.sqlite"],
        "completed-original",
    ) == []
    assert smoke._unexpected_state_changes(
        "completed-project-projection-refresh",
        ["events.jsonl"],
        "completed-original",
    ) == ["events.jsonl"]


def test_release_capability_template_passes_smoke_gate() -> None:
    manual_docs = _load_manual_docs()
    template = ROOT / "docs" / "manual" / "reference" / "release-capability-template.md"

    assert manual_docs.release_errors(template, ["controlled-workflow-start"]) == []
