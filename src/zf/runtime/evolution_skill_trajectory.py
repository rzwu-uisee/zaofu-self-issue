"""Normalized provider trajectories and outcome-independent Skill behavior verdicts."""

from __future__ import annotations

import json
import re
import shlex
from collections import Counter
from copy import deepcopy
from pathlib import Path
from typing import Any, Mapping, Sequence

from zf.runtime.evolution_contracts import EvolutionContractError, stable_digest


TRAJECTORY_SCHEMA = "skill-provider-trajectory.v1"
BEHAVIOR_VERDICT_SCHEMA = "skill-behavior-verdict.v1"

_OPERATORS = frozenset({"eq", "gte", "lte"})
_DESTRUCTIVE = re.compile(
    r"(?:^|\s)(?:rm\s+-rf|git\s+reset\s+--hard|git\s+clean\s+-[a-z]*f|mkfs\b|shutdown\b)"
)


def normalize_provider_trajectory(
    *,
    case_id: str,
    backend: str,
    stdout: str,
    stderr: str,
    final: str,
    skill_load_evidence: Sequence[Mapping[str, Any]] = (),
    workspace_root: str = "",
) -> dict[str, Any]:
    """Reduce provider streams to stable, secret-minimizing behavioral evidence."""

    normalized_case_id = str(case_id or "").strip()
    if not normalized_case_id:
        raise EvolutionContractError("Skill trajectory case_id is required")
    steps: list[dict[str, Any]] = []
    load_lines = {
        int(item.get("line") or 0)
        for item in skill_load_evidence
        if str(item.get("line") or "").isdigit()
    }
    records = _provider_records(stdout, stderr)
    for source_line, record in records:
        item = record.get("item") if isinstance(record.get("item"), Mapping) else record
        item = dict(item)
        event_type = str(record.get("type") or item.get("type") or "provider_event")
        command = str(item.get("command") or record.get("command") or "")
        tool_name = str(
            item.get("tool_name") or item.get("name") or record.get("tool_name") or ""
        ).strip()
        action_type = _action_type(event_type, item, command, tool_name)
        status = _step_status(record, item)
        step = {
            "index": len(steps) + 1,
            "source_line": source_line,
            "event_type": event_type,
            "action_type": action_type,
            "tool_name": tool_name,
            "script_name": _script_name(command),
            "input_digest": stable_digest(_redacted_input(item, command)),
            "observation_digest": stable_digest(_redacted_observation(record, item)),
            "status": status,
            "destructive": bool(_DESTRUCTIVE.search(command.lower())),
            "workspace_escape": _workspace_escape(command, workspace_root),
            "target_skill_read": source_line in load_lines,
        }
        step["step_id"] = "step-" + stable_digest(step)[:20]
        steps.append(step)

    represented_load_lines = {
        int(step["source_line"]) for step in steps if bool(step["target_skill_read"])
    }
    for evidence in skill_load_evidence:
        source_line = int(evidence.get("line") or 0)
        if source_line in represented_load_lines:
            continue
        step = {
            "index": len(steps) + 1,
            "source_line": source_line,
            "event_type": str(evidence.get("kind") or "provider_skill_read"),
            "action_type": "skill_read",
            "tool_name": "",
            "script_name": "",
            "input_digest": str(
                evidence.get("digest") or stable_digest(dict(evidence))
            ),
            "observation_digest": stable_digest({"observed": True}),
            "status": "completed",
            "destructive": False,
            "workspace_escape": False,
            "target_skill_read": True,
        }
        step["step_id"] = "step-" + stable_digest(step)[:20]
        steps.append(step)

    steps.sort(key=lambda row: (int(row["source_line"] or 10**9), int(row["index"])))
    for index, step in enumerate(steps, start=1):
        step["index"] = index
    final_step = {
        "index": len(steps) + 1,
        "source_line": 0,
        "event_type": "provider_final",
        "action_type": "final_output",
        "tool_name": "",
        "script_name": "",
        "input_digest": stable_digest({"case_id": normalized_case_id}),
        "observation_digest": stable_digest(str(final or "")),
        "status": "completed" if str(final or "").strip() else "failed",
        "destructive": False,
        "workspace_escape": False,
        "target_skill_read": False,
    }
    final_step["step_id"] = "step-" + stable_digest(final_step)[:20]
    steps.append(final_step)
    metrics, metric_step_refs = _trajectory_metrics(steps)
    body = {
        "schema_version": TRAJECTORY_SCHEMA,
        "case_id": normalized_case_id,
        "backend": str(backend or "").strip(),
        "steps": steps,
        "metrics": metrics,
        "metric_step_refs": metric_step_refs,
    }
    body["trajectory_digest"] = stable_digest(body)
    return body


def evaluate_trajectory_behavior(
    case: Mapping[str, Any],
    trajectory: Mapping[str, Any],
) -> dict[str, Any]:
    """Evaluate explicit process expectations without consulting outcome scores."""

    if trajectory.get("schema_version") != TRAJECTORY_SCHEMA:
        raise EvolutionContractError("Skill behavior requires a normalized trajectory")
    case_id = str(case.get("case_id") or "").strip()
    if case_id != str(trajectory.get("case_id") or ""):
        raise EvolutionContractError("Skill behavior trajectory case_id drift")
    expectations = case.get("behavior_expectations")
    if expectations is None:
        expectations = []
    if not isinstance(expectations, list):
        raise EvolutionContractError("behavior_expectations must be a list")
    metrics = trajectory.get("metrics")
    refs = trajectory.get("metric_step_refs")
    if not isinstance(metrics, Mapping) or not isinstance(refs, Mapping):
        raise EvolutionContractError("Skill trajectory metrics are invalid")
    checks: list[dict[str, Any]] = []
    for index, item in enumerate(expectations, start=1):
        if not isinstance(item, Mapping):
            raise EvolutionContractError("behavior expectation entries must be objects")
        metric = str(item.get("metric") or "").strip()
        operator = str(item.get("operator") or "eq").strip()
        expectation_id = str(item.get("id") or f"behavior-{index}").strip()
        if metric not in metrics:
            raise EvolutionContractError(f"unknown Skill behavior metric: {metric}")
        if operator not in _OPERATORS:
            raise EvolutionContractError(f"unsupported behavior operator: {operator}")
        expected = deepcopy(item.get("value"))
        observed = deepcopy(metrics[metric])
        passed = _compare(observed, operator, expected)
        checks.append(
            {
                "id": expectation_id,
                "metric": metric,
                "operator": operator,
                "expected": expected,
                "observed": observed,
                "passed": passed,
                "trajectory_step_refs": list(refs.get(metric) or []),
            }
        )
    followed = all(bool(item["passed"]) for item in checks) if checks else None
    body = {
        "schema_version": BEHAVIOR_VERDICT_SCHEMA,
        "case_id": case_id,
        "trajectory_digest": str(trajectory.get("trajectory_digest") or ""),
        "behavior_followed": followed,
        "checks": checks,
    }
    body["verdict_digest"] = stable_digest(body)
    return body


def _provider_records(stdout: str, stderr: str) -> list[tuple[int, dict[str, Any]]]:
    rows: list[tuple[int, dict[str, Any]]] = []
    for line_number, line in enumerate((stdout + "\n" + stderr).splitlines(), start=1):
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, Mapping):
            rows.append((line_number, dict(value)))
    return rows


def _action_type(
    event_type: str,
    item: Mapping[str, Any],
    command: str,
    tool_name: str,
) -> str:
    lowered = f"{event_type} {item.get('type', '')}".lower()
    if command:
        return "command"
    if tool_name or "tool" in lowered:
        return "tool"
    if "error" in lowered or "failed" in lowered:
        return "error"
    if "message" in lowered or "reason" in lowered:
        return "reasoning"
    return "provider_event"


def _step_status(record: Mapping[str, Any], item: Mapping[str, Any]) -> str:
    value = str(item.get("status") or record.get("status") or "").lower()
    event = str(record.get("type") or "").lower()
    exit_code = item.get("exit_code", record.get("exit_code"))
    if value in {"failed", "error", "cancelled"} or "failed" in event:
        return "failed"
    if isinstance(exit_code, int) and exit_code != 0:
        return "failed"
    if value in {"completed", "succeeded", "success"} or "completed" in event:
        return "completed"
    return "observed"


def _redacted_input(item: Mapping[str, Any], command: str) -> dict[str, Any]:
    return {
        "type": str(item.get("type") or ""),
        "command_digest": stable_digest(command) if command else "",
        "tool_name": str(item.get("tool_name") or item.get("name") or ""),
    }


def _redacted_observation(
    record: Mapping[str, Any],
    item: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        "event_type": str(record.get("type") or ""),
        "status": str(item.get("status") or record.get("status") or ""),
        "exit_code": item.get("exit_code", record.get("exit_code")),
    }


def _script_name(command: str) -> str:
    if not command:
        return ""
    try:
        parts = shlex.split(command)
    except ValueError:
        return ""
    for item in parts:
        if item.endswith((".py", ".sh", ".js", ".ts")):
            return Path(item).name
    return ""


def _workspace_escape(command: str, workspace_root: str) -> bool:
    if not command:
        return False
    if "../" in command or "..\\" in command:
        return True
    if not workspace_root:
        return False
    root = str(Path(workspace_root).resolve(strict=False))
    system_executable = _system_executable(command)
    absolute_paths = re.findall(r"(?:^|\s)(/[A-Za-z0-9_./-]+)", command)
    return any(
        path != system_executable and not path.startswith(root + "/") and path != root
        for path in absolute_paths
    )


def _system_executable(command: str) -> str:
    try:
        executable = shlex.split(command)[0]
    except (IndexError, ValueError):
        return ""
    trusted_roots = ("/bin/", "/usr/bin/", "/usr/local/bin/")
    return executable if executable.startswith(trusted_roots) else ""


def _trajectory_metrics(
    steps: Sequence[Mapping[str, Any]],
) -> tuple[dict[str, Any], dict[str, list[str]]]:
    load_steps = [item for item in steps if bool(item.get("target_skill_read"))]
    work_steps = [
        item
        for item in steps
        if item.get("action_type") in {"command", "tool"}
        and not bool(item.get("target_skill_read"))
    ]
    failed = [item for item in steps if item.get("status") == "failed"]
    completed_after_failure = bool(
        failed
        and any(
            int(item.get("index") or 0) > int(failed[0].get("index") or 0)
            and item.get("status") == "completed"
            and item.get("action_type") in {"command", "tool"}
            for item in steps
        )
    )
    commands = [
        str(item.get("input_digest") or "")
        for item in work_steps
        if str(item.get("input_digest") or "")
    ]
    counts = Counter(commands)
    destructive = [item for item in steps if bool(item.get("destructive"))]
    escaped = [item for item in steps if bool(item.get("workspace_escape"))]
    first_load = int(load_steps[0]["index"]) if load_steps else 0
    first_work = int(work_steps[0]["index"]) if work_steps else 0
    metrics: dict[str, Any] = {
        "activation": bool(load_steps),
        "skill_read_before_action": (
            first_load < first_work if first_load and first_work else None
        ),
        "script_execution_count": sum(
            bool(item.get("script_name")) for item in work_steps
        ),
        "failure_count": len(failed),
        "recovery_after_failure": completed_after_failure if failed else None,
        "tool_call_count": len(work_steps),
        "duplicate_tool_call_count": sum(
            count - 1 for count in counts.values() if count > 1
        ),
        "destructive_action_count": len(destructive),
        "workspace_escape_count": len(escaped),
        "security_clear": not destructive and not escaped,
    }
    refs = {
        "activation": _step_ids(load_steps),
        "skill_read_before_action": _step_ids([*load_steps[:1], *work_steps[:1]]),
        "script_execution_count": _step_ids(
            [item for item in work_steps if item.get("script_name")]
        ),
        "failure_count": _step_ids(failed),
        "recovery_after_failure": _step_ids(
            [
                item
                for item in steps
                if item.get("status") in {"failed", "completed"}
                and item.get("action_type") in {"command", "tool", "error"}
            ]
        ),
        "tool_call_count": _step_ids(work_steps),
        "duplicate_tool_call_count": _step_ids(work_steps),
        "destructive_action_count": _step_ids(destructive),
        "workspace_escape_count": _step_ids(escaped),
        "security_clear": _step_ids([*destructive, *escaped]),
    }
    return metrics, refs


def _step_ids(steps: Sequence[Mapping[str, Any]]) -> list[str]:
    return [str(item.get("step_id") or "") for item in steps if item.get("step_id")]


def _compare(observed: Any, operator: str, expected: Any) -> bool:
    if observed is None:
        return False
    if operator == "eq":
        return observed == expected
    if isinstance(observed, bool) or isinstance(expected, bool):
        raise EvolutionContractError("gte/lte behavior expectations must be numeric")
    try:
        left = float(observed)
        right = float(expected)
    except (TypeError, ValueError) as exc:
        raise EvolutionContractError(
            "gte/lte behavior expectations must be numeric"
        ) from exc
    return left >= right if operator == "gte" else left <= right


__all__ = [
    "BEHAVIOR_VERDICT_SCHEMA",
    "TRAJECTORY_SCHEMA",
    "evaluate_trajectory_behavior",
    "normalize_provider_trajectory",
]
