"""Retry briefing renderer for one existing fanout child."""

from __future__ import annotations

import json
import shlex
from pathlib import Path
from typing import Any

from zf.core.config.schema import RoleConfig
from zf.runtime.cli_command import zf_cli_cmd
from zf.runtime.stage_failure_replan import reader_stage_lineage_payload
from zf.runtime.workflow_inputs import render_workflow_input_briefing_section


def write_fanout_retry_briefing(
    runtime: Any,
    *,
    role: RoleConfig,
    manifest: dict,
    child: dict,
    run_id: str,
) -> Path:
    briefings_dir = runtime.state_dir / "briefings"
    briefings_dir.mkdir(parents=True, exist_ok=True)
    fanout_id = str(manifest.get("fanout_id") or "")
    child_id = str(child.get("child_id") or "")
    path = briefings_dir / f"{role.instance_id}-{fanout_id}-{child_id}-retry.md"
    aggregate_config = (
        manifest.get("aggregate_config")
        if isinstance(manifest.get("aggregate_config"), dict)
        else {}
    )
    child_payload = (
        dict(child.get("payload"))
        if isinstance(child.get("payload"), dict)
        else {}
    )
    lineage = reader_stage_lineage_payload(
        runtime.event_log.read_all(),
        stage_id=str(manifest.get("stage_id") or ""),
        correlation_id=str(manifest.get("trace_id") or ""),
    )
    current_trigger = (
        child_payload.get("trigger_payload")
        if isinstance(child_payload.get("trigger_payload"), dict)
        else {}
    )
    child_payload = {**lineage, **child_payload}
    child_payload["trigger_payload"] = {
        **lineage,
        **current_trigger,
    }
    child_success_event, child_failure_event = (
        runtime._fanout_child_result_events(aggregate_config)
    )
    success_payload = {
        "fanout_id": fanout_id,
        "stage_id": str(manifest.get("stage_id") or ""),
        "child_id": child_id,
        "run_id": run_id,
        "role_instance": role.instance_id,
        "status": "completed",
        "report": {
            "child_id": child_id,
            "status": "passed",
            "summary": "Short retry outcome summary.",
            "findings": [],
            "recommendation": "approve",
        },
    }
    success_event = str(aggregate_config.get("success_event") or "")
    is_plan_artifact_stage = runtime._is_plan_artifact_stage(
        role=role,
        stage_id=str(manifest.get("stage_id") or ""),
        success_event=success_event,
        child_success_event=child_success_event,
    )
    retry_contract_lines: list[str] = []
    if is_plan_artifact_stage:
        plan_ref = (
            f"docs/plans/{manifest.get('stage_id', '')}-"
            f"{child_id}-plan.md"
        )
        task_map_ref = (
            "artifacts/plan/task_map.json"
            if success_event == "task_map.ready"
            else ""
        )
        artifact_refs = [ref for ref in (plan_ref, task_map_ref) if ref]
        success_payload.update({
            "plan_artifact_ref": plan_ref,
            "task_map_ref": task_map_ref,
            "artifact_refs": artifact_refs,
            "evidence_refs": [],
            "plan_ports": [],
        })
        success_payload["report"].update({
            "plan_artifact_ref": plan_ref,
            "task_map_ref": task_map_ref,
            "artifact_refs": artifact_refs,
            "evidence_refs": [],
            "plan_ports": [],
        })
        retry_contract_lines = [
            "",
            "## Planning Retry Contract",
            "",
            *runtime._plan_artifact_contract_lines(),
            *runtime._plan_port_contract_lines(
                flow_kind=str(
                    child_payload.get("flow_kind")
                    or current_trigger.get("flow_kind")
                    or ""
                )
            ),
        ]
        if task_map_ref:
            retry_contract_lines.extend([
                "Write the machine-readable task map to the workdir-relative "
                f"path `{task_map_ref}` and report that same ref; the kernel "
                "relocates it before synth.",
                "This remains task-map synthesis, not findings-only triage. "
                "Do not emit success until the task map and every required "
                "ready plan_port are present.",
            ])
    failure_payload = {
        **success_payload,
        "status": "failed",
        "reason": "Retry could not complete the assigned child scope.",
        "report": {
            "child_id": child_id,
            "status": "failed",
            "summary": "Short retry failure summary.",
            "findings": [],
            "recommendation": "reject",
        },
    }
    workflow_input_section = render_workflow_input_briefing_section(
        child_payload,
    ).strip()
    workflow_input_lines = (
        ["", *workflow_input_section.splitlines()]
        if workflow_input_section
        else []
    )

    def emit_command(event_type: str, payload: dict) -> str:
        if not event_type:
            return "# no event configured"
        cli_parts = shlex.split(zf_cli_cmd()) or ["zf"]
        return " ".join([
            *[shlex.quote(part) for part in cli_parts],
            "emit",
            shlex.quote(event_type),
            "--actor",
            shlex.quote(role.instance_id),
            "--state-dir",
            shlex.quote(str(runtime.state_dir)),
            "--payload",
            shlex.quote(json.dumps(payload, ensure_ascii=False)),
        ])

    lines: list[str] = []
    if manifest.get("topology") == "fanout_writer_scoped":
        task_id = str(child.get("task_id") or "")
        if task_id:
            lines.append(f"Active task: {task_id}")
    lines.extend([
        f"# Fanout Retry: {child_id}",
        "",
        f"- fanout_id: `{fanout_id}`",
        f"- stage_id: `{manifest.get('stage_id', '')}`",
        f"- child_id: `{child_id}`",
        f"- run_id: `{run_id}`",
        f"- target_ref: `{manifest.get('target_ref', '')}`",
        "",
        "This is a retry of the same fanout child. Keep the same child_id and use the new run_id.",
    ])
    if manifest.get("topology") == "fanout_writer_scoped":
        lines.extend([
            f"- task_id: `{child.get('task_id', '')}`",
            f"- workdir: `{child.get('workdir', '')}`",
            f"- worker_branch: `{child.get('source_branch', '')}`",
            "",
            f"Emit dev.build.done with `--state-dir {runtime.state_dir}` and the same fanout_id and child_id when finished.",
        ])
    else:
        lines.extend([
            "",
            "Child payload:",
            "```json",
            json.dumps(child_payload, indent=2, ensure_ascii=False),
            "```",
            "",
            "Aggregate contract:",
            "```json",
            json.dumps(aggregate_config, indent=2, ensure_ascii=False),
            "```",
            *workflow_input_lines,
            *retry_contract_lines,
            "",
            "Success command:",
            "```bash",
            emit_command(child_success_event, success_payload),
            "```",
            "",
            "Failure command:",
            "```bash",
            emit_command(child_failure_event, failure_payload),
            "```",
            "",
            "Do not emit the aggregate success/failure event directly; the kernel publishes it after the fanout barrier or synth role finishes.",
            "Emit-once protocol: the result event is consumed asynchronously — you will",
            "NOT receive an acknowledgement. Emitting succeeds when the command exits 0.",
            "NEVER re-emit the same completion (no retry loops, no periodic re-sends):",
            "if this fanout generation was superseded, every duplicate is marked",
            "stale_completion and discarded, and re-sending floods the event log",
            "(r10 forensics: one lane re-emitting every ~7s produced 4.5k junk rows).",
            "After emitting once, stop and wait for new instructions.",
            "`fanout_id`, `stage_id`, `child_id`, `run_id`, `role_instance`, and `status` must stay as top-level payload fields.",
            "Finding schema: use `severity` = info|low|medium|high|critical, `path`, `message`, and optional integer `line`.",
        ])
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path
