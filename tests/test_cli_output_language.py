"""Regression checks for the public CLI language and character set."""

from __future__ import annotations

import argparse
import ast
from pathlib import Path
import re

from zf.cli.main import build_parser


SOURCE_ROOT = Path(__file__).parents[1] / "src" / "zf"
CLI_OUTPUT_DIRS = (
    SOURCE_ROOT / "cli",
    SOURCE_ROOT / "core" / "config",
    SOURCE_ROOT / "core" / "events",
)
CLI_OUTPUT_FILES = (
    SOURCE_ROOT / "core" / "profile" / "recommender.py",
    SOURCE_ROOT / "core" / "task" / "contract_validation.py",
    SOURCE_ROOT / "core" / "workflow" / "inspection.py",
    SOURCE_ROOT / "core" / "workflow" / "inspection_render.py",
    SOURCE_ROOT / "core" / "workflow" / "lane_pipeline.py",
    SOURCE_ROOT / "core" / "workflow" / "lane_role_template.py",
    SOURCE_ROOT / "core" / "workflow" / "workflow_kind.py",
    SOURCE_ROOT / "runtime" / "env_preflight.py",
)
IMPLEMENTATION_REFERENCE = re.compile(
    r"(?i)(?:\bdoc(?:ument)?\s*[-#:]?\s*\d+|section\s+\d+|§|R\d{2,}|ISSUE-\d+)"
)


def _docstring_nodes(tree: ast.AST) -> set[int]:
    nodes: set[int] = set()
    for node in ast.walk(tree):
        body = getattr(node, "body", None)
        if (
            isinstance(body, list)
            and body
            and isinstance(body[0], ast.Expr)
            and isinstance(body[0].value, ast.Constant)
            and isinstance(body[0].value.value, str)
        ):
            nodes.add(id(body[0].value))
    return nodes


def _output_source_paths() -> list[Path]:
    paths = [
        path
        for source_dir in CLI_OUTPUT_DIRS
        for path in sorted(source_dir.rglob("*.py"))
    ]
    paths.extend(CLI_OUTPUT_FILES)
    return paths


def test_all_cli_help_is_ascii() -> None:
    pending = [build_parser()]
    visited: set[int] = set()
    violations: list[str] = []

    while pending:
        parser = pending.pop()
        if id(parser) in visited:
            continue
        visited.add(id(parser))

        help_text = parser.format_help()
        if not help_text.isascii():
            violations.append(parser.prog)

        for action in parser._actions:
            if isinstance(action, argparse._SubParsersAction):
                pending.extend(action.choices.values())

    assert not violations, f"non-ASCII CLI help: {violations}"


def test_cli_output_source_string_literals_are_ascii() -> None:
    violations: list[str] = []

    for path in _output_source_paths():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        docstrings = _docstring_nodes(tree)
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Constant)
                and isinstance(node.value, str)
                and id(node) not in docstrings
                and not node.value.isascii()
            ):
                violations.append(f"{path.name}:{node.lineno}")

    assert not violations, f"non-ASCII CLI runtime strings: {violations}"


def test_cli_output_source_strings_hide_implementation_references() -> None:
    violations: list[str] = []

    for path in _output_source_paths():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        docstrings = _docstring_nodes(tree)
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Constant)
                and isinstance(node.value, str)
                and id(node) not in docstrings
                and IMPLEMENTATION_REFERENCE.search(node.value)
            ):
                violations.append(f"{path.name}:{node.lineno}")

    assert not violations, f"implementation references in CLI runtime strings: {violations}"
