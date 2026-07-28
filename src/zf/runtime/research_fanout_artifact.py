"""Durable artifact projection for completed research fanouts."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from zf.core.events.model import ZfEvent
from zf.core.state.atomic_io import atomic_write_text


_SAFE_ID_RE = re.compile(r"[^A-Za-z0-9._-]+")


def materialize_research_fanout_artifact(
    state_dir: Path,
    *,
    manifest: dict[str, Any],
    synth_event: ZfEvent,
) -> dict[str, Any]:
    """Write one stable research synthesis and return its artifact descriptor."""
    stage_id = str(manifest.get("stage_id") or "").strip()
    if not _is_research_stage(stage_id):
        return {}

    synth = synth_event.payload if isinstance(synth_event.payload, dict) else {}
    report = synth.get("report") if isinstance(synth.get("report"), dict) else {}
    summary = str(
        synth.get("research_summary")
        or synth.get("summary")
        or report.get("summary")
        or ""
    ).strip()
    if not summary:
        return {}

    trigger = (
        manifest.get("trigger_payload")
        if isinstance(manifest.get("trigger_payload"), dict)
        else {}
    )
    source_refs = (
        trigger.get("source_refs")
        if isinstance(trigger.get("source_refs"), dict)
        else {}
    )
    task_id = str(
        trigger.get("task_id")
        or manifest.get("task_id")
        or synth_event.task_id
        or "unbound"
    ).strip()
    fanout_id = str(
        manifest.get("fanout_id")
        or synth.get("fanout_id")
        or synth_event.id
    ).strip()
    topic = str(
        source_refs.get("topic")
        or trigger.get("reason")
        or trigger.get("expected_output")
        or stage_id
    ).strip()
    evidence_refs = _string_refs(
        synth.get("evidence_refs"),
        report.get("evidence_refs"),
    )
    open_questions = _strings(
        synth.get("open_questions")
        or report.get("open_questions")
    )
    children = _child_reports(manifest)

    body = _render_markdown(
        task_id=task_id,
        stage_id=stage_id,
        fanout_id=fanout_id,
        workflow_run_id=str(trigger.get("workflow_run_id") or ""),
        topic=topic,
        summary=summary,
        evidence_refs=evidence_refs,
        open_questions=open_questions,
        children=children,
        prd_prompt_input=str(synth.get("prd_prompt_input") or ""),
        refactor_prompt_input=str(synth.get("refactor_prompt_input") or ""),
        synth_event_id=synth_event.id,
    )
    relative_ref = (
        Path("research")
        / _safe_id(task_id)
        / f"{_safe_id(fanout_id)}.md"
    )
    target = Path(state_dir) / relative_ref
    atomic_write_text(target, body)
    digest = hashlib.sha256(body.encode("utf-8")).hexdigest()
    return {
        "artifact_id": f"research-{_safe_id(fanout_id)}",
        "kind": "research_report",
        "name": target.name,
        "ref": relative_ref.as_posix(),
        "path": relative_ref.as_posix(),
        "sha256": digest,
        "hash": f"sha256:{digest}",
        "summary": summary,
        "task_id": task_id,
        "stage_id": stage_id,
        "fanout_id": fanout_id,
        "workflow_run_id": str(trigger.get("workflow_run_id") or ""),
        "request_id": str(trigger.get("request_id") or ""),
        "request_revision": trigger.get("request_revision") or "",
        "synth_event_id": synth_event.id,
    }


def merge_research_artifact_payload(
    payload: dict[str, Any],
    descriptor: dict[str, Any],
) -> dict[str, Any]:
    if not descriptor:
        return payload
    refs = list(payload.get("artifact_refs") or [])
    digest = str(descriptor.get("sha256") or "")
    if not any(
        isinstance(item, dict)
        and str(item.get("sha256") or item.get("hash") or "").removeprefix("sha256:")
        == digest
        for item in refs
    ):
        refs.append(dict(descriptor))
    return {
        **payload,
        "artifact_refs": refs,
        "research_artifact_ref": str(descriptor.get("ref") or ""),
        "research_artifact_digest": digest,
        "research_summary": str(descriptor.get("summary") or ""),
    }


def _render_markdown(
    *,
    task_id: str,
    stage_id: str,
    fanout_id: str,
    workflow_run_id: str,
    topic: str,
    summary: str,
    evidence_refs: list[str],
    open_questions: list[str],
    children: list[dict[str, str]],
    prd_prompt_input: str,
    refactor_prompt_input: str,
    synth_event_id: str,
) -> str:
    lines = [
        f"# Research synthesis: {topic}",
        "",
        "## Identity",
        "",
        f"- task_id: `{task_id}`",
        f"- stage_id: `{stage_id}`",
        f"- fanout_id: `{fanout_id}`",
        f"- workflow_run_id: `{workflow_run_id}`",
        f"- synth_event_id: `{synth_event_id}`",
        "",
        "## Summary",
        "",
        summary,
        "",
        "## Evidence refs",
        "",
    ]
    lines.extend(f"- `{item}`" for item in evidence_refs)
    if not evidence_refs:
        lines.append("- No external ref was supplied; child event provenance is retained below.")
    lines.extend(["", "## Child findings", ""])
    for child in children:
        lines.append(
            f"- **{child['child_id']}** (`{child['result_event_id']}`): "
            f"{child['summary']}"
        )
    if not children:
        lines.append("- No child report was projected.")
    lines.extend(["", "## Open questions", ""])
    lines.extend(f"- {item}" for item in open_questions)
    if not open_questions:
        lines.append("- None.")
    if prd_prompt_input:
        lines.extend(["", "## PRD prompt input", "", prd_prompt_input])
    if refactor_prompt_input:
        lines.extend(["", "## Refactor prompt input", "", refactor_prompt_input])
    return "\n".join(lines).rstrip() + "\n"


def _child_reports(manifest: dict[str, Any]) -> list[dict[str, str]]:
    reports: list[dict[str, str]] = []
    for child in manifest.get("children") or []:
        if not isinstance(child, dict):
            continue
        report = child.get("report") if isinstance(child.get("report"), dict) else {}
        payload = child.get("payload") if isinstance(child.get("payload"), dict) else {}
        nested_report = (
            payload.get("report")
            if isinstance(payload.get("report"), dict)
            else {}
        )
        summary = str(
            child.get("summary")
            or report.get("summary")
            or payload.get("summary")
            or nested_report.get("summary")
            or ""
        ).strip()
        reports.append({
            "child_id": str(child.get("child_id") or child.get("role_instance") or "child"),
            "result_event_id": str(
                child.get("result_event_id")
                or payload.get("result_event_id")
                or child.get("last_event_id")
                or ""
            ),
            "summary": summary or "No summary supplied.",
        })
    return reports


def _string_refs(*values: Any) -> list[str]:
    out: list[str] = []
    for value in values:
        for item in _strings(value):
            if item not in out:
                out.append(item)
    return out


def _strings(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value.strip()] if value.strip() else []
    if isinstance(value, (list, tuple, set)):
        out: list[str] = []
        for item in value:
            if isinstance(item, dict):
                text = str(
                    item.get("ref")
                    or item.get("path")
                    or item.get("uri")
                    or json.dumps(item, sort_keys=True, ensure_ascii=False)
                ).strip()
            else:
                text = str(item or "").strip()
            if text:
                out.append(text)
        return out
    text = str(value).strip()
    return [text] if text else []


def _safe_id(value: str) -> str:
    return _SAFE_ID_RE.sub("-", str(value or "")).strip("-._") or "research"


def _is_research_stage(stage_id: str) -> bool:
    lowered = stage_id.lower()
    return "research" in lowered or "autoresearch" in lowered
