"""Project-level instruction document scaffolding for ZaoFu init."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from zf.core.agents_md import render_canonical_block, replace_managed_block
from zf.core.config.schema import ZfConfig
from zf.core.profile.apply import (
    PROFILE_BLOCK_END,
    PROFILE_BLOCK_START,
    render_stack_section,
)
from zf.core.profile.detector import declared_profile, detect
from zf.core.profile.schema import ProjectProfile


PROJECT_CONTEXT_BLOCK_START = "<!-- ZF:PROJECT-CONTEXT:START -->"
PROJECT_CONTEXT_BLOCK_END = "<!-- ZF:PROJECT-CONTEXT:END -->"
_KERNEL_BLOCK_START = "<!-- ZF:START -->"


@dataclass(frozen=True)
class ProjectInstructionDocsResult:
    created: tuple[str, ...] = ()
    updated: tuple[str, ...] = ()
    skipped: tuple[str, ...] = ()
    profile: dict[str, Any] = field(default_factory=dict)


def ensure_project_instruction_docs(
    project_root: Path,
    *,
    config: ZfConfig | None,
    state_dir: Path,
    stack: str = "",
    surface: str = "",
) -> ProjectInstructionDocsResult:
    """Create or refresh root AGENTS.md / CLAUDE.md for a ZaoFu project."""
    root = Path(project_root).resolve()
    project_name = config.project.name if config is not None else root.name
    project_description = (
        config.project.description if config is not None else ""
    )
    state_ref = _display_state_dir(root, state_dir)
    profile = (
        declared_profile(stack, surface)
        if stack
        else detect(root)
    )

    created: list[str] = []
    updated: list[str] = []
    skipped: list[str] = []

    agents_path = root / "AGENTS.md"
    agents_created, agents_updated = _ensure_agents_md(
        agents_path,
        project_name=project_name,
        project_description=project_description,
        state_ref=state_ref,
        config=config,
        profile=profile,
    )
    if agents_created:
        created.append("AGENTS.md")
    elif agents_updated:
        updated.append("AGENTS.md")
    else:
        skipped.append("AGENTS.md")

    claude_path = root / "CLAUDE.md"
    if claude_path.exists():
        skipped.append("CLAUDE.md")
    else:
        claude_path.write_text(
            render_project_claude_md(project_name=project_name, state_ref=state_ref),
            encoding="utf-8",
        )
        created.append("CLAUDE.md")

    return ProjectInstructionDocsResult(
        created=tuple(created),
        updated=tuple(updated),
        skipped=tuple(skipped),
        profile=profile.to_dict(),
    )


def render_project_agents_md_shell(*, project_name: str, state_ref: str) -> str:
    """Return editable project guidance above the ZaoFu managed block."""
    return f"""# AGENTS.md

本仓库使用 ZaoFu 作为 multi-agent harness。

## Project Rules

- 项目名: `{project_name}`。
- `zf.yaml` 是唯一 ZaoFu 控制面配置。
- `project.state_dir` 当前解析为 `{state_ref}`;这是运行态目录,不是源码。
- 不要直接改写 `events.jsonl`、`kanban.json`、`session.yaml`、`feature_list.json`、`role_sessions.yaml`。
- 状态变更优先走 `zf` CLI、受控事件写入或 kernel helper。
- `events.jsonl` 记录 append-only 发生/因果/裁决引用;canonical stores 持有当前状态;
  required artifact/sidecar 持有完整语义或大证据。不要把三者互相冒充。
- Web/API/集成侧只做受控 action 或只读 projection,不要绕过 kernel 写业务状态。
- 开发、review、测试、交付报告默认使用中文,除非项目另有明确约定。

## Verification

- 修改 `zf.yaml`、运行态协议、Web/API 或 orchestration 行为后,运行对应的 focused test。
- 无法运行验证时,在交付说明里写清楚阻塞项和原计划命令。

## Harness Health Signals

- `zf validate --instructions` 通过。
- `zf update agents-md --check` 通过。
- 每个 accepted task 都有明确 verification evidence。
- event ledger、canonical stores 和 required artifacts 只能通过各自受控 writer 变更。
- long-running work 留下 heartbeat、handoff 或 recovery evidence。
"""


def render_project_claude_md(*, project_name: str, state_ref: str) -> str:
    """Return a Claude-specific bridge that points back to AGENTS.md."""
    return f"""# CLAUDE.md

本项目使用 ZaoFu 管理 multi-agent 开发流程。

## Claude Code Rules

- 开始工作前先阅读 `AGENTS.md`。
- 项目名: `{project_name}`。
- `zf.yaml` 是唯一 ZaoFu 控制面配置。
- `project.state_dir` 当前解析为 `{state_ref}`;不要把运行态文件当作源码维护。
- 不要直接写 `events.jsonl`、`kanban.json`、`session.yaml`、`feature_list.json`、`role_sessions.yaml`。
- 状态变更通过 `zf` CLI、受控事件写入或 kernel helper 完成。
- 普通交互式开发会话没有 `Active task: <task_id>` briefing 时,不要自行 emit
  task/workflow event 或 heartbeat。
- 修改代码时保持范围收敛,优先沿用项目现有模式。
- 交付前运行项目约定的测试;无法运行时说明阻塞项。
"""


def render_project_context_section(
    *,
    project_name: str,
    project_description: str,
) -> str:
    """Render durable, provider-neutral Project context for coding agents."""

    lines = [
        PROJECT_CONTEXT_BLOCK_START,
        "## Project Context (managed by ZaoFu)",
        "",
        f"- 项目名: `{project_name}`",
    ]
    description = _escape_instruction_markers(project_description.strip())
    if description:
        lines.extend(["", "### 背景与目标", ""])
        lines.extend(
            ">" if not line else f"> {line}"
            for line in description.splitlines()
        )
    lines.append(PROJECT_CONTEXT_BLOCK_END)
    return "\n".join(lines)


def _ensure_agents_md(
    path: Path,
    *,
    project_name: str,
    project_description: str,
    state_ref: str,
    config: ZfConfig | None,
    profile: ProjectProfile,
) -> tuple[bool, bool]:
    existed = path.exists()
    current = path.read_text(encoding="utf-8") if existed else ""
    base = current if existed else render_project_agents_md_shell(
        project_name=project_name,
        state_ref=state_ref,
    )
    updated = _replace_instruction_section(
        base,
        start=PROJECT_CONTEXT_BLOCK_START,
        end=PROJECT_CONTEXT_BLOCK_END,
        section=render_project_context_section(
            project_name=project_name,
            project_description=project_description,
        ),
    )
    if profile.languages or profile.confidence == "declared":
        updated = _replace_instruction_section(
            updated,
            start=PROFILE_BLOCK_START,
            end=PROFILE_BLOCK_END,
            section=render_stack_section(profile),
        )
    updated = replace_managed_block(
        updated,
        render_canonical_block(config=config).rstrip("\n"),
    )
    if updated == current:
        return False, False
    path.write_text(updated, encoding="utf-8")
    return (not existed), existed


def _replace_instruction_section(
    text: str,
    *,
    start: str,
    end: str,
    section: str,
) -> str:
    start_count = text.count(start)
    end_count = text.count(end)
    if start_count != end_count or start_count > 1:
        raise ValueError(f"malformed managed instruction section: {start}")
    if start_count == 1:
        before, remainder = text.split(start, 1)
        _, after = remainder.split(end, 1)
        return f"{before.rstrip()}\n\n{section}\n\n{after.lstrip()}".rstrip() + "\n"
    if _KERNEL_BLOCK_START in text:
        before, after = text.split(_KERNEL_BLOCK_START, 1)
        return (
            f"{before.rstrip()}\n\n{section}\n\n"
            f"{_KERNEL_BLOCK_START}{after}"
        )
    separator = "\n\n" if text.strip() else ""
    return f"{text.rstrip()}{separator}{section}\n"


def _escape_instruction_markers(value: str) -> str:
    return (
        value
        .replace(PROJECT_CONTEXT_BLOCK_START, "&lt;!-- ZF:PROJECT-CONTEXT:START --&gt;")
        .replace(PROJECT_CONTEXT_BLOCK_END, "&lt;!-- ZF:PROJECT-CONTEXT:END --&gt;")
    )


def _display_state_dir(project_root: Path, state_dir: Path) -> str:
    resolved = Path(state_dir).resolve()
    try:
        return resolved.relative_to(project_root.resolve()).as_posix()
    except ValueError:
        return str(resolved)
