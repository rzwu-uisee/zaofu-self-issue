#!/usr/bin/env python3
"""Generate and validate task-oriented manual reference artifacts."""

from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import unquote

import yaml


ROOT = Path(__file__).resolve().parents[1]
MANUAL_DIR = ROOT / "docs" / "manual"
REFERENCE_DIR = MANUAL_DIR / "reference"
COVERAGE_SOURCE = REFERENCE_DIR / "capability-coverage.yaml"
GENERATED_FILES = {
    REFERENCE_DIR / "cli-command-index.md": "cli-zh",
    REFERENCE_DIR / "cli-command-index.en.md": "cli-en",
    REFERENCE_DIR / "capability-coverage.md": "coverage-zh",
    REFERENCE_DIR / "capability-coverage.en.md": "coverage-en",
}
ALLOWED_COVERAGE_STATUSES = {
    "implemented",
    "partial",
    "candidate",
    "historical",
    "superseded",
}
REQUIRED_RELEASE_FIELDS = ("activation", "readback", "rollback", "authority")
MARKDOWN_LINK_RE = re.compile(r"!?(?:\[[^\]]*\])\(([^)]+)\)")
CAPABILITY_BLOCK_RE = re.compile(
    r"<!--\s*ZF-CAPABILITY:\s*([a-z0-9][a-z0-9-]*)\s*-->"
    r"(.*?)"
    r"<!--\s*ZF-CAPABILITY-END\s*-->",
    re.DOTALL,
)
RELEASE_LABELS = {
    "Activation / 启用": re.compile(r"^\s*-\s*Activation / 启用:\s*\S", re.MULTILINE),
    "Readback / 回读": re.compile(r"^\s*-\s*Readback / 回读:\s*\S", re.MULTILINE),
    "Rollback / 回退": re.compile(r"^\s*-\s*Rollback / 回退:\s*\S", re.MULTILINE),
    "Authority / 权限边界": re.compile(r"^\s*-\s*Authority / 权限边界:\s*\S", re.MULTILINE),
    "Manual / 文档": re.compile(r"^\s*-\s*Manual / 文档:\s*\S", re.MULTILINE),
}
STALE_MANUAL_PATTERNS = {
    "Channel templates must not all auto-fanout": re.compile(
        r"(?:所有|全部|均使用|all use|all templates).*fanout_then_synthesis",
        re.IGNORECASE,
    ),
    "Product Flow must not assign global authority to Layer 2": re.compile(
        r"Layer 2 orchestrator(?: agent)?\s*\|\s*(?:拆解目标|Goal decomposition)",
        re.IGNORECASE,
    ),
}


def _subparser_actions(parser: argparse.ArgumentParser) -> list[argparse._SubParsersAction]:
    return [
        action
        for action in parser._actions
        if isinstance(action, argparse._SubParsersAction)
    ]


def _choice_help(action: argparse._SubParsersAction) -> dict[str, str]:
    result: dict[str, str] = {}
    for choice in action._choices_actions:
        help_text = choice.help
        if isinstance(help_text, str) and help_text != argparse.SUPPRESS:
            result[str(choice.dest)] = help_text
    return result


def _command_children(
    parser: argparse.ArgumentParser,
) -> list[tuple[str, argparse.ArgumentParser, str]]:
    children: list[tuple[str, argparse.ArgumentParser, str]] = []
    for action in _subparser_actions(parser):
        help_by_name = _choice_help(action)
        for name, child in sorted(action.choices.items()):
            summary = help_by_name.get(name) or child.description or ""
            children.append((name, child, str(summary).strip()))
    return children


def _walk_commands(
    parser: argparse.ArgumentParser,
    prefix: tuple[str, ...] = ("zf",),
) -> Iterable[tuple[tuple[str, ...], str]]:
    for name, child, summary in _command_children(parser):
        path = (*prefix, name)
        yield path, summary
        yield from _walk_commands(child, path)


def _table_text(value: str) -> str:
    compact = " ".join(value.split()) or "No parser summary."
    return compact.replace("|", "\\|").replace("`", "\\`")


def render_cli_inventory(language: str) -> str:
    from zf.cli.main import build_parser

    parser = build_parser()
    entries = list(_walk_commands(parser))
    direct = _command_children(parser)
    descendants_by_family: dict[str, list[tuple[tuple[str, ...], str]]] = {
        name: [] for name, _, _ in direct
    }
    for path, summary in entries:
        descendants_by_family[path[1]].append((path, summary))

    if language == "zh":
        lines = [
            "# ZaoFu CLI 命令目录",
            "",
            "> 本文件由 `src/zf/cli/main.py::build_parser()` 生成，禁止手工修改。",
            "> 重新生成：`uv run python scripts/manual-docs.py generate`。",
            "",
            f"当前共 **{len(direct)}** 个顶层命令 family、**{len(entries)}** 条可寻址命令路径。",
            "命令描述直接取自 argparse parser，因此描述语言以代码中的 help 为准。",
            "",
        ]
    else:
        lines = [
            "# ZaoFu CLI Command Inventory",
            "",
            "> Generated from `src/zf/cli/main.py::build_parser()`; do not edit by hand.",
            "> Regenerate with `uv run python scripts/manual-docs.py generate`.",
            "",
            f"The parser currently exposes **{len(direct)}** top-level families and **{len(entries)}** addressable command paths.",
            "Descriptions come directly from argparse help text.",
            "",
        ]

    for family, _, family_summary in direct:
        lines.extend(
            [
                f"## `zf {family}`",
                "",
                _table_text(family_summary),
                "",
                "| Command | Parser description |",
                "|---|---|",
            ]
        )
        for path, summary in descendants_by_family[family]:
            lines.append(f"| `{' '.join(path)}` | {_table_text(summary)} |")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def load_coverage() -> dict[str, Any]:
    data = yaml.safe_load(COVERAGE_SOURCE.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("capability coverage must be a YAML object")
    return data


def _repo_path(value: Any) -> Path:
    return ROOT / str(value)


def _relative_link(repo_path: str, output: Path) -> str:
    return Path(os.path.relpath(ROOT / repo_path, output.parent)).as_posix()


def _markdown_links(paths: list[str], output: Path) -> str:
    return "<br>".join(
        f"[`{Path(path).name}`]({_relative_link(path, output)})" for path in paths
    )


def render_coverage(language: str) -> str:
    data = load_coverage()
    capabilities = data.get("capabilities", [])
    output = REFERENCE_DIR / (
        "capability-coverage.md" if language == "zh" else "capability-coverage.en.md"
    )
    if language == "zh":
        lines = [
            "# ZaoFu 能力覆盖清单",
            "",
            "> 从 `capability-coverage.yaml` 生成，禁止手工修改。",
            "> 它是发布面能力到 manual/code/test 的证据目录，不是全仓库模块清单。",
            "",
            f"最后人工核实：`{data.get('last_verified', '-')}`。",
            "",
            "| 能力 | 状态 | 用户手册 | 实现 | 测试 |",
            "|---|---|---|---|---|",
        ]
    else:
        lines = [
            "# ZaoFu Capability Coverage Catalog",
            "",
            "> Generated from `capability-coverage.yaml`; do not edit by hand.",
            "> This maps release-facing capabilities to manual/code/test evidence; it is not a module inventory.",
            "",
            f"Last manually verified: `{data.get('last_verified', '-')}`.",
            "",
            "| Capability | Status | Manual | Implementation | Tests |",
            "|---|---|---|---|---|",
        ]

    for capability in capabilities:
        manuals = capability["manuals"]
        manual_paths = list(manuals.get("zh", [])) + list(manuals.get("en", []))
        name = capability["name"] if language == "zh" else capability["name_en"]
        lines.append(
            "| "
            + " | ".join(
                [
                    f"`{capability['id']}`<br>{_table_text(str(name))}",
                    f"`{capability['status']}`",
                    _markdown_links(manual_paths, output),
                    "<br>".join(f"`{path}`" for path in capability["code"]),
                    "<br>".join(f"`{path}`" for path in capability["tests"]),
                ]
            )
            + " |"
        )

    title = "发布 Smoke 元数据" if language == "zh" else "Release Smoke Metadata"
    lines.extend(["", f"## {title}", ""])
    for capability in capabilities:
        name = capability["name"] if language == "zh" else capability["name_en"]
        release = capability["release"]
        lines.extend(
            [
                f"### `{capability['id']}` - {name}",
                "",
                f"- **Activation**: {release['activation']}",
                f"- **Readback**: {release['readback']}",
                f"- **Rollback**: {release['rollback']}",
                f"- **Authority**: {release['authority']}",
                "",
            ]
        )
    return "\n".join(lines).rstrip() + "\n"


def generated_content(kind: str) -> str:
    if kind == "cli-zh":
        return render_cli_inventory("zh")
    if kind == "cli-en":
        return render_cli_inventory("en")
    if kind == "coverage-zh":
        return render_coverage("zh")
    if kind == "coverage-en":
        return render_coverage("en")
    raise ValueError(f"unknown generated document kind: {kind}")


def generate() -> None:
    for path, kind in GENERATED_FILES.items():
        path.write_text(generated_content(kind), encoding="utf-8")
        print(path.relative_to(ROOT))


def _extract_link_target(raw: str) -> str:
    value = raw.strip()
    if value.startswith("<") and ">" in value:
        return value[1 : value.index(">")]
    return value.split(maxsplit=1)[0] if value else ""


def _resolve_local_link(source: Path, raw: str) -> Path | None:
    target = unquote(_extract_link_target(raw))
    if not target or target.startswith("#"):
        return None
    if re.match(r"^[a-z][a-z0-9+.-]*:", target, re.IGNORECASE):
        return None
    target = target.split("#", 1)[0].split("?", 1)[0]
    if not target:
        return None
    if target.startswith("/"):
        return ROOT / target.lstrip("/")
    return (source.parent / target).resolve()


def _linked_manual_docs(source: Path) -> list[Path]:
    result: list[Path] = []
    for raw in MARKDOWN_LINK_RE.findall(source.read_text(encoding="utf-8")):
        target = _resolve_local_link(source, raw)
        if target is not None and target.suffix == ".md" and target.is_relative_to(MANUAL_DIR):
            result.append(target)
    return result


def _reachable_manual_docs(start: Path) -> set[Path]:
    seen: set[Path] = set()
    queue = [start.resolve()]
    while queue:
        current = queue.pop()
        if current in seen or not current.exists():
            continue
        seen.add(current)
        queue.extend(path for path in _linked_manual_docs(current) if path not in seen)
    return seen


def coverage_errors(data: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if data.get("schema_version") != "zf-manual-capability-coverage.v1":
        errors.append("coverage schema_version must be zf-manual-capability-coverage.v1")
    capabilities = data.get("capabilities")
    if not isinstance(capabilities, list) or not capabilities:
        return [*errors, "coverage capabilities must be a non-empty list"]

    ids: set[str] = set()
    reachable_zh = _reachable_manual_docs(MANUAL_DIR / "00-index.md")
    reachable_en = _reachable_manual_docs(MANUAL_DIR / "00-index.en.md")
    for index, capability in enumerate(capabilities):
        label = f"capabilities[{index}]"
        if not isinstance(capability, dict):
            errors.append(f"{label} must be an object")
            continue
        capability_id = str(capability.get("id") or "").strip()
        label = capability_id or label
        if not re.fullmatch(r"[a-z0-9][a-z0-9-]*", capability_id):
            errors.append(f"{label}: invalid id")
        if capability_id in ids:
            errors.append(f"{label}: duplicate id")
        ids.add(capability_id)
        if not str(capability.get("name") or "").strip() or not str(
            capability.get("name_en") or ""
        ).strip():
            errors.append(f"{label}: name and name_en are required")
        if capability.get("status") not in ALLOWED_COVERAGE_STATUSES:
            errors.append(f"{label}: unsupported status {capability.get('status')!r}")

        manuals = capability.get("manuals")
        if not isinstance(manuals, dict):
            errors.append(f"{label}: manuals must be an object")
            manuals = {}
        for language, reachable in (("zh", reachable_zh), ("en", reachable_en)):
            paths = manuals.get(language)
            if not isinstance(paths, list) or not paths:
                errors.append(f"{label}: manuals.{language} must be non-empty")
                continue
            for path_value in paths:
                path = _repo_path(path_value).resolve()
                if not path.exists():
                    errors.append(f"{label}: missing manuals.{language} path {path_value}")
                elif path not in reachable:
                    errors.append(
                        f"{label}: manuals.{language} path is not reachable from its manual index: {path_value}"
                    )

        for field in ("code", "tests"):
            paths = capability.get(field)
            if not isinstance(paths, list) or not paths:
                errors.append(f"{label}: {field} must be a non-empty list")
                continue
            for path_value in paths:
                if not _repo_path(path_value).exists():
                    errors.append(f"{label}: missing {field} path {path_value}")

        release = capability.get("release")
        if not isinstance(release, dict):
            errors.append(f"{label}: release must be an object")
            continue
        for field in REQUIRED_RELEASE_FIELDS:
            if not str(release.get(field) or "").strip():
                errors.append(f"{label}: release.{field} is required")
    return errors


def link_errors() -> list[str]:
    errors: list[str] = []
    for source in sorted(MANUAL_DIR.rglob("*.md")):
        text = source.read_text(encoding="utf-8")
        for raw in MARKDOWN_LINK_RE.findall(text):
            target = _resolve_local_link(source, raw)
            if target is not None and not target.exists():
                errors.append(
                    f"{source.relative_to(ROOT)}: broken local link {_extract_link_target(raw)!r}"
                )
    return errors


def stale_manual_errors() -> list[str]:
    errors: list[str] = []
    for source in sorted(MANUAL_DIR.rglob("*.md")):
        text = source.read_text(encoding="utf-8")
        for reason, pattern in STALE_MANUAL_PATTERNS.items():
            if pattern.search(text):
                errors.append(f"{source.relative_to(ROOT)}: {reason}")
    return errors


def design_reference_errors() -> list[str]:
    errors: list[str] = []
    pattern = re.compile(r"(?:\.\./)+design/|docs/design/")
    for source in sorted(MANUAL_DIR.rglob("*.md")):
        if pattern.search(source.read_text(encoding="utf-8")):
            errors.append(
                f"{source.relative_to(ROOT)}: user manual must not depend on docs/design"
            )
    return errors


def command_contract_errors() -> list[str]:
    scripts_dir = str(ROOT / "scripts")
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)
    from manual_commands import (
        command_matrix_errors,
        local_command_reference_errors,
        manual_command_contract,
    )

    errors, _stats = manual_command_contract()
    errors.extend(command_matrix_errors())
    errors.extend(local_command_reference_errors())
    return errors


def currentness_errors() -> list[str]:
    errors: list[str] = []
    try:
        data = load_coverage()
    except (OSError, ValueError, yaml.YAMLError) as exc:
        return [f"cannot load coverage catalog: {exc}"]
    errors.extend(coverage_errors(data))
    for path, kind in GENERATED_FILES.items():
        expected = generated_content(kind)
        if not path.exists():
            errors.append(f"missing generated document {path.relative_to(ROOT)}")
        elif path.read_text(encoding="utf-8") != expected:
            errors.append(
                f"stale generated document {path.relative_to(ROOT)}; run scripts/manual-docs.py generate"
            )
    errors.extend(link_errors())
    errors.extend(stale_manual_errors())
    errors.extend(design_reference_errors())
    errors.extend(command_contract_errors())
    return errors


def release_errors(release_notes: Path, surfaces: list[str]) -> list[str]:
    errors: list[str] = []
    try:
        data = load_coverage()
    except (OSError, ValueError, yaml.YAMLError) as exc:
        return [f"cannot load coverage catalog: {exc}"]
    by_id = {
        capability["id"]: capability
        for capability in data.get("capabilities", [])
        if isinstance(capability, dict) and capability.get("id")
    }
    if not release_notes.exists():
        return [f"release notes do not exist: {release_notes}"]
    text = release_notes.read_text(encoding="utf-8")
    blocks = {capability_id: body for capability_id, body in CAPABILITY_BLOCK_RE.findall(text)}
    for surface in surfaces:
        capability = by_id.get(surface)
        if capability is None:
            errors.append(f"unknown capability surface: {surface}")
            continue
        body = blocks.get(surface)
        if body is None:
            errors.append(f"release notes missing ZF-CAPABILITY block for {surface}")
            continue
        for label, pattern in RELEASE_LABELS.items():
            if not pattern.search(body):
                errors.append(f"{surface}: missing non-empty {label} field")
        manual_targets: set[Path] = set()
        for raw in MARKDOWN_LINK_RE.findall(body):
            target = _resolve_local_link(release_notes, raw)
            if target is not None:
                manual_targets.add(target.resolve())
        expected_manuals = {
            _repo_path(path).resolve()
            for paths in capability["manuals"].values()
            for path in paths
        }
        if not manual_targets.intersection(expected_manuals):
            errors.append(f"{surface}: Manual / 文档 must link a cataloged manual")
    return errors


def _print_result(errors: list[str], success: str) -> int:
    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1
    print(success)
    return 0


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("generate", help="regenerate CLI and capability reference pages")
    subparsers.add_parser(
        "check",
        help="check generated docs, coverage, links, stale contracts, and CLI examples",
    )
    release = subparsers.add_parser(
        "release-check",
        help="validate capability announcement blocks in release notes",
    )
    release.add_argument("--release-notes", type=Path, required=True)
    release.add_argument("--surface", action="append", required=True, dest="surfaces")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    if args.command == "generate":
        generate()
        return 0
    if args.command == "check":
        return _print_result(currentness_errors(), "manual docs currentness: ok")
    if args.command == "release-check":
        notes = args.release_notes
        if not notes.is_absolute():
            notes = ROOT / notes
        return _print_result(
            release_errors(notes.resolve(), args.surfaces),
            "release capability documentation: ok",
        )
    raise AssertionError(f"unhandled command: {args.command}")


if __name__ == "__main__":
    raise SystemExit(main())
