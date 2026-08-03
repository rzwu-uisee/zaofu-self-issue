#!/usr/bin/env python3
"""Validate ZaoFu CLI invocations embedded in manual shell fences."""

from __future__ import annotations

import argparse
import re
import shlex
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import yaml


ROOT = Path(__file__).resolve().parents[1]
MANUAL_DIR = ROOT / "docs" / "manual"
COMMAND_MATRIX = MANUAL_DIR / "reference" / "command-validation-matrix.yaml"
COMMAND_MATRIX_SCHEMA = "zf-manual-command-validation.v1"
SHELL_FENCE_LANGUAGES = {"bash", "console", "sh", "shell", "zsh"}
FENCE_RE = re.compile(r"^\s*(`{3,}|~{3,})\s*([^\s`]*)")
SHELL_CONTROL_TOKENS = {"&", "&&", "(", ")", ";", "|", "||"}
REDIRECTION_RE = re.compile(r"^\d*[<>]")
PLACEHOLDER_COMMAND_RE = re.compile(r"^(?:<[^>]+>|\$\{?COMMAND\}?|COMMAND)$", re.I)
REPO_COMMAND_PREFIXES = ("scripts/", "tests/", "tools/")


@dataclass(frozen=True)
class CommandSnippet:
    source: Path
    line: int
    command: str
    argv: tuple[str, ...]

    @property
    def location(self) -> str:
        return f"{self.source.relative_to(ROOT)}:{self.line}"


@dataclass(frozen=True)
class CommandContractStats:
    snippets: int
    unique_paths: int
    skipped_generic: int


def _shell_tokens(command: str) -> list[str]:
    lexer = shlex.shlex(command, posix=True, punctuation_chars=";&|()")
    lexer.whitespace_split = True
    lexer.commenters = "#"
    return list(lexer)


def _shell_blocks(source: Path) -> Iterable[tuple[int, list[str]]]:
    marker = ""
    language = ""
    start_line = 0
    block: list[str] = []
    for line_number, line in enumerate(
        source.read_text(encoding="utf-8").splitlines(), start=1
    ):
        if not marker:
            match = FENCE_RE.match(line)
            if match is None:
                continue
            marker = match.group(1)
            language = match.group(2).lower()
            start_line = line_number + 1
            block = []
            continue
        if line.lstrip().startswith(marker[0] * len(marker)):
            if language in SHELL_FENCE_LANGUAGES:
                yield start_line, block
            marker = ""
            language = ""
            block = []
            continue
        block.append(line)


def _logical_shell_commands(
    lines: list[str], start_line: int
) -> Iterable[tuple[int, str, list[str]]]:
    buffered: list[str] = []
    logical_start = start_line
    for offset, line in enumerate(lines):
        if not buffered:
            logical_start = start_line + offset
        stripped = line.rstrip()
        continued = stripped.endswith("\\")
        if continued:
            stripped = stripped[:-1]
        buffered.append(stripped)
        command = "\n".join(buffered)
        if continued:
            continue
        try:
            tokens = _shell_tokens(command)
        except ValueError:
            continue
        yield logical_start, command, tokens
        buffered = []
    if buffered:
        command = "\n".join(buffered)
        try:
            tokens = _shell_tokens(command)
        except ValueError:
            return
        yield logical_start, command, tokens


def _command_argvs(tokens: list[str]) -> Iterable[tuple[str, ...]]:
    starts: list[int] = []
    for index, token in enumerate(tokens):
        if token == "zf":
            if index > 0 and tokens[index - 1] in {"echo", "printf", "which"}:
                continue
            starts.append(index + 1)
        elif token == "zf.cli.main" and index > 0 and tokens[index - 1] == "-m":
            starts.append(index + 1)
    for start in starts:
        argv: list[str] = []
        for token in tokens[start:]:
            if token in SHELL_CONTROL_TOKENS:
                break
            if REDIRECTION_RE.match(token) and not PLACEHOLDER_COMMAND_RE.match(token):
                break
            argv.append(token)
        yield tuple(argv)


def collect_manual_commands(manual_dir: Path = MANUAL_DIR) -> list[CommandSnippet]:
    snippets: list[CommandSnippet] = []
    for source in sorted(manual_dir.rglob("*.md")):
        for start_line, block in _shell_blocks(source):
            for line, command, tokens in _logical_shell_commands(block, start_line):
                for argv in _command_argvs(tokens):
                    snippets.append(
                        CommandSnippet(
                            source=source,
                            line=line,
                            command=command,
                            argv=argv,
                        )
                    )
    return snippets


def local_command_reference_errors(manual_dir: Path = MANUAL_DIR) -> list[str]:
    errors: list[str] = []
    seen: set[tuple[Path, int, str]] = set()
    for source in sorted(manual_dir.rglob("*.md")):
        for start_line, block in _shell_blocks(source):
            for line, _command, tokens in _logical_shell_commands(block, start_line):
                for token in tokens:
                    normalized = token.removeprefix("./")
                    for root_prefix in ("$ZAOFU_ROOT/", "${ZAOFU_ROOT}/"):
                        if normalized.startswith(root_prefix):
                            normalized = normalized[len(root_prefix) :]
                    if not normalized.startswith(REPO_COMMAND_PREFIXES):
                        continue
                    if any(marker in normalized for marker in ("<", ">", "*", "{")):
                        continue
                    key = (source, line, normalized)
                    if key in seen:
                        continue
                    seen.add(key)
                    if not (ROOT / normalized).is_file():
                        errors.append(
                            f"{source.relative_to(ROOT)}:{line}: "
                            f"missing repo-local command path {normalized!r}"
                        )
    return errors


def _subparsers(parser: argparse.ArgumentParser) -> argparse._SubParsersAction | None:
    return next(
        (
            action
            for action in parser._actions
            if isinstance(action, argparse._SubParsersAction)
        ),
        None,
    )


def _options(parser: argparse.ArgumentParser) -> dict[str, argparse.Action]:
    return {
        option: action
        for action in parser._actions
        for option in action.option_strings
    }


def _option_name(token: str) -> str:
    return token.split("=", 1)[0]


def _option_value_count(action: argparse.Action, token: str) -> int:
    if "=" in token or action.nargs == 0:
        return 0
    if action.nargs in (None, 1, "?"):
        return 1
    if isinstance(action.nargs, int):
        return action.nargs
    return 0


def validate_command_argv(
    argv: tuple[str, ...], parser: argparse.ArgumentParser
) -> tuple[str | None, tuple[str, ...]]:
    """Return an error and resolved command path for one normalized invocation."""

    if not argv:
        return None, ("zf",)
    if PLACEHOLDER_COMMAND_RE.match(argv[0]):
        return None, ()

    current = parser
    chain = [parser]
    path = ["zf"]
    index = 0
    while index < len(argv):
        token = argv[index]
        if token in SHELL_CONTROL_TOKENS or token == "...":
            break
        if token.startswith("-") and not re.match(r"^-\d", token):
            option = _option_name(token)
            action = _options(current).get(option)
            if action is None:
                break
            index += 1 + _option_value_count(action, token)
            continue

        subparsers = _subparsers(current)
        if subparsers is None:
            break
        if PLACEHOLDER_COMMAND_RE.match(token):
            return None, ()
        child = subparsers.choices.get(token)
        if child is None:
            return (
                f"unknown subcommand {token!r} below {' '.join(path)}",
                tuple(path),
            )
        current = child
        chain.append(child)
        path.append(token)
        index += 1

    if len(path) == 1 and argv and argv[0] not in {"--help", "-h", "--version"}:
        return f"unknown top-level command {argv[0]!r}", tuple(path)

    allowed_options = {
        option
        for command_parser in chain
        for option in _options(command_parser)
    }
    for token in argv:
        if not token.startswith("-") or re.match(r"^-\d", token):
            continue
        option = _option_name(token)
        if option not in allowed_options:
            return (
                f"unknown option {option!r} for {' '.join(path)}",
                tuple(path),
            )
    return None, tuple(path)


def manual_command_contract(
    manual_dir: Path = MANUAL_DIR,
) -> tuple[list[str], CommandContractStats]:
    from zf.cli.main import build_parser

    parser = build_parser()
    errors: list[str] = []
    paths: set[tuple[str, ...]] = set()
    skipped_generic = 0
    snippets = collect_manual_commands(manual_dir)
    for snippet in snippets:
        error, path = validate_command_argv(snippet.argv, parser)
        if not path:
            skipped_generic += 1
        else:
            paths.add(path)
        if error:
            errors.append(f"{snippet.location}: {error}: {snippet.command!r}")
    return errors, CommandContractStats(
        snippets=len(snippets),
        unique_paths=len(paths),
        skipped_generic=skipped_generic,
    )


def command_matrix_errors(matrix_path: Path = COMMAND_MATRIX) -> list[str]:
    from zf.cli.main import build_parser

    try:
        data = yaml.safe_load(matrix_path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        return [f"cannot load command validation matrix: {exc}"]
    if not isinstance(data, dict):
        return ["command validation matrix must be a YAML object"]
    errors: list[str] = []
    if data.get("schema_version") != COMMAND_MATRIX_SCHEMA:
        errors.append(f"command validation matrix schema must be {COMMAND_MATRIX_SCHEMA}")
    cases = data.get("cases")
    if not isinstance(cases, list) or not cases:
        return [*errors, "command validation matrix cases must be non-empty"]
    parser = build_parser()
    case_ids: set[str] = set()
    for index, case in enumerate(cases):
        label = f"cases[{index}]"
        if not isinstance(case, dict):
            errors.append(f"{label} must be an object")
            continue
        case_id = str(case.get("id") or "")
        label = case_id or label
        if not re.fullmatch(r"[a-z0-9][a-z0-9-]*", case_id):
            errors.append(f"{label}: invalid id")
        if case_id in case_ids:
            errors.append(f"{label}: duplicate id")
        case_ids.add(case_id)
        if not str(case.get("execution_class") or ""):
            errors.append(f"{label}: execution_class is required")
        command = case.get("command")
        if not isinstance(command, list) or not command:
            errors.append(f"{label}: command must be a non-empty list")
        else:
            error, _ = validate_command_argv(tuple(map(str, command)), parser)
            if error:
                errors.append(f"{label}: {error}")
        documents = case.get("documents")
        if not isinstance(documents, list) or not documents:
            errors.append(f"{label}: documents must be a non-empty list")
            continue
        for document in documents:
            path = ROOT / str(document)
            if not path.is_file():
                errors.append(f"{label}: missing source document {document}")
    return errors


def main() -> int:
    errors, stats = manual_command_contract()
    errors.extend(command_matrix_errors())
    errors.extend(local_command_reference_errors())
    if errors:
        for error in errors:
            print(f"ERROR: {error}")
        return 1
    print(
        "manual CLI command contract: ok "
        f"({stats.snippets} snippets, {stats.unique_paths} command paths, "
        f"{stats.skipped_generic} generic examples skipped)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
