"""Canonical read-only project status replies for Kanban Agent surfaces."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from zf.core.events import EventLog, ZfEvent
from zf.runtime.channel_prd_context import canonical_channel_prd_context
from zf.runtime.channel_projection import project_channels
from zf.runtime.kanban_proposals import (
    canonical_proposal_action,
    pending_kanban_proposals,
)
from zf.runtime.operator_intent import infer_operator_intent


_WORKFLOW_PROPOSAL_ACTIONS = frozenset({
    "workflow-start",
    "workflow-submit",
    "idea-to-product",
})
_WORKFLOW_LIFECYCLE_EVENTS = frozenset({
    "workflow.request.proposed",
    "workflow.request.approved",
    "workflow.submit.accepted",
    "workflow.invoke.requested",
})


def is_project_status_query(message: str, *, project_id: str = "") -> bool:
    """Return true only for a low-risk, no-action status intent."""

    intent = infer_operator_intent(
        message,
        project_id=project_id,
        source="kanban-agent",
    )
    return (
        str(intent.get("intent_type") or "") == "project_status_query"
        and not list(intent.get("proposed_actions") or [])
        and not bool(intent.get("requires_confirmation"))
    )


def render_project_status_reply(state_dir: Path) -> str:
    """Render a bounded status response from canonical projections only."""

    state = Path(state_dir)
    events = EventLog(state / "events.jsonl").read_all()
    channels = list(project_channels(state, events=events).get("channels") or [])
    product_channels = [
        item for item in channels
        if isinstance(item, dict) and not _is_transport_control_channel(item)
    ]
    if not product_channels:
        product_channels = [item for item in channels if isinstance(item, dict)]
    prds = list(canonical_channel_prd_context(state).get("items") or [])
    proposals = pending_kanban_proposals(events)
    workflow_proposals = [
        item for item in proposals
        if canonical_proposal_action(str(item.get("action") or ""))
        in _WORKFLOW_PROPOSAL_ACTIONS
    ]
    workflow_events = [
        item for item in events if item.type in _WORKFLOW_LIFECYCLE_EVENTS
    ]

    lines = ["当前项目状态（基于 canonical event ledger）："]
    lines.append(_channel_line(product_channels, events))
    lines.append(_prd_line(prds))
    lines.append(_proposal_line("Task proposal", proposals))
    lines.append(_workflow_line(workflow_proposals, workflow_events))
    lines.append("本次为只读状态查询：未创建、修改、批准或启动任何任务与工作流。")
    return "\n".join(lines)


def _channel_line(channels: list[dict[str, Any]], events: list[ZfEvent]) -> str:
    if not channels:
        return "- Channel：0 个业务 Channel。"
    rows = []
    for channel in channels[:3]:
        channel_id = str(channel.get("channel_id") or channel.get("id") or "")
        name = str(channel.get("name") or channel_id or "未命名 Channel")
        rows.append(
            f"{name}（{channel_id}，{_discussion_state(events, channel_id)}）"
        )
    suffix = f"；另有 {len(channels) - 3} 个" if len(channels) > 3 else ""
    return f"- Channel：{len(channels)} 个，" + "；".join(rows) + suffix + "。"


def _prd_line(prds: list[dict[str, Any]]) -> str:
    if not prds:
        return "- PRD：0 个 canonical PRD（尚未达到 ready）。"
    rows = [
        f"{str(item.get('channel_name') or item.get('channel_id') or '未命名')}"
        f"（{str(item.get('channel_id') or '')}）"
        for item in prds[:3]
        if isinstance(item, dict)
    ]
    return f"- PRD：{len(prds)} 个 canonical PRD，" + "；".join(rows) + "。"


def _proposal_line(label: str, proposals: list[dict[str, Any]]) -> str:
    task_proposals = [
        item for item in proposals
        if canonical_proposal_action(str(item.get("action") or ""))
        not in _WORKFLOW_PROPOSAL_ACTIONS
    ]
    if not task_proposals:
        return f"- {label}：0 个待批准提案。"
    rows = []
    for item in task_proposals[:3]:
        action = str(item.get("action") or "未命名 action")
        title = str(item.get("title") or "未命名提案")
        rows.append(f"{title}（{action}）")
    suffix = f"；另有 {len(task_proposals) - 3} 个" if len(task_proposals) > 3 else ""
    return f"- {label}：{len(task_proposals)} 个待批准，" + "；".join(rows) + suffix + "。"


def _workflow_line(
    proposals: list[dict[str, Any]],
    lifecycle_events: list[ZfEvent],
) -> str:
    if not proposals and not lifecycle_events:
        return "- Workflow：无待批准 workflow proposal，且尚未提交或启动 workflow。"
    parts = []
    if proposals:
        parts.append(f"{len(proposals)} 个待批准 workflow proposal")
    if lifecycle_events:
        latest = lifecycle_events[-1]
        parts.append(f"最近事件为 {latest.type}")
    return "- Workflow：" + "；".join(parts) + "。"


def _discussion_state(events: list[ZfEvent], channel_id: str) -> str:
    state = "已创建，未启动讨论"
    for event in events:
        payload = event.payload if isinstance(event.payload, dict) else {}
        event_channel_id = str(payload.get("channel_id") or event.correlation_id or "")
        if event_channel_id != channel_id:
            continue
        if event.type == "channel.discussion.started":
            state = "已启动讨论"
        elif event.type == "channel.discussion.closed":
            state = "讨论已关闭"
    return state


def _is_transport_control_channel(channel: dict[str, Any]) -> bool:
    channel_id = str(channel.get("channel_id") or channel.get("id") or "")
    return channel_id.startswith(("feishu-kanban_agent-", "feishu-run_manager-"))


__all__ = ["is_project_status_query", "render_project_status_reply"]
