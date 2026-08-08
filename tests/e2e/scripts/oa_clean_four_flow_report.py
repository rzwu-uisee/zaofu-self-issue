#!/usr/bin/env python3
"""Combine clean-checkout Product and General real-provider evidence."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Mapping


SCHEMA_VERSION = "oa-clean-four-flow-e2e-report.v1"
_PRODUCT_FLOWS = ("prd", "issue", "refactor")


def build_four_flow_report(
    product_report_path: Path,
    general_report_path: Path,
) -> dict[str, Any]:
    product_report = _read(product_report_path)
    general = _read(general_report_path)
    product_runs = product_report.get("runs")
    if not isinstance(product_runs, list):
        product_runs = []
    by_name = {
        str(run.get("name") or ""): run
        for run in product_runs
        if isinstance(run, Mapping)
    }
    runs = [
        _product_run(flow, by_name.get(flow, {}))
        for flow in _PRODUCT_FLOWS
    ]
    runs.append(_general_run(general))

    source_commits = {
        str(run.get("source_commit") or "") for run in runs
    }
    same_source = len(source_commits) == 1 and "" not in source_commits
    clean_source = all(bool(run.get("source_clean")) for run in runs)
    passed = same_source and clean_source and all(
        run.get("status") == "passed" for run in runs
    )
    return {
        "schema_version": SCHEMA_VERSION,
        "status": "passed" if passed else "failed",
        "source_commit": next(iter(source_commits)) if same_source else "",
        "source_identity_closed": same_source and clean_source,
        "summary": {
            "flow_count": 4,
            "passed": sum(run.get("status") == "passed" for run in runs),
            "failed": sum(run.get("status") != "passed" for run in runs),
        },
        "runs": runs,
        "limitations": [
            "PRD/Issue/Refactor use full resident-provider Product Flows",
            "General uses deterministic workflow closure plus one real Verify turn",
        ],
    }


def _product_run(flow: str, value: Mapping[str, Any]) -> dict[str, Any]:
    identity = value.get("source_identity")
    identity = identity if isinstance(identity, Mapping) else {}
    context = value.get("context_handoff")
    context = context if isinstance(context, Mapping) else {}
    checks = context.get("checks")
    checks = checks if isinstance(checks, Mapping) else {}
    terminal = value.get("terminal_delivery")
    terminal = terminal if isinstance(terminal, Mapping) else {}
    closed = (
        value.get("status") == "passed"
        and terminal.get("status") == "passed"
        and bool(checks)
        and all(bool(item) for item in checks.values())
    )
    return {
        "flow_kind": flow,
        "execution_mode": "full_real_provider",
        "status": "passed" if closed else "failed",
        "source_commit": str(identity.get("zaofu_commit") or ""),
        "source_clean": bool(identity.get("zaofu_clean")),
        "state_dir": str(value.get("state_dir") or ""),
        "prompt_sha256": str(value.get("prompt_sha256") or ""),
        "config": dict(value.get("config") or {}),
        "provider": dict(value.get("provider") or {}),
        "budget": dict(value.get("budget") or {}),
        "duration_seconds": value.get("duration_seconds", 0),
        "usage": dict(value.get("usage") or {}),
        "attempts": dict(value.get("attempts") or {}),
        "terminal_delivery": terminal,
        "context_handoff": context,
        "failure_classification": str(
            value.get("failure_classification") or ""
        ),
    }


def _general_run(value: Mapping[str, Any]) -> dict[str, Any]:
    identity = value.get("source_identity")
    identity = identity if isinstance(identity, Mapping) else {}
    checks = {
        "artifact_delivery_terminal": bool(value.get("terminal_event_id")),
        "dossier_ready": value.get("dossier_status") == "ready",
        "required_artifacts": bool(value.get("required_artifact_refs")),
        "config_pinned": bool(value.get("effective_config_digest")),
        "run_contract_pinned": bool(value.get("run_contract_digest")),
        "stage_graph": value.get("stage_graph") == [
            "scope",
            "collect-a",
            "collect-b",
            "synthesize",
            "verify",
        ],
        "real_provider_turn": bool(value.get("provider_session_id")),
        "prompt_pinned": bool(value.get("prompt_sha256")),
    }
    closed = value.get("status") == "passed" and all(checks.values())
    return {
        "flow_kind": "general",
        "execution_mode": str(value.get("execution_mode") or ""),
        "status": "passed" if closed else "failed",
        "source_commit": str(identity.get("head_commit") or ""),
        "source_clean": not bool(identity.get("dirty", True)),
        "prompt_sha256": str(value.get("prompt_sha256") or ""),
        "config": {
            "effective_config_digest": str(
                value.get("effective_config_digest") or ""
            ),
            "run_contract_digest": str(
                value.get("run_contract_digest") or ""
            ),
            "completion_profile": str(
                value.get("completion_profile") or ""
            ),
        },
        "provider": {
            "backend": str(value.get("backend") or ""),
            "model": str(value.get("model") or ""),
            "reasoning_effort": str(
                value.get("reasoning_effort") or ""
            ),
        },
        "budget": dict(value.get("budget") or {}),
        "duration_seconds": value.get("duration_seconds", 0),
        "usage": dict(value.get("usage") or {}),
        "context_checks": checks,
        "semantic_replan_count": int(
            value.get("semantic_replan_count") or 0
        ),
        "protocol_repair_count": int(
            value.get("protocol_repair_count") or 0
        ),
        "cleaned": bool(value.get("cleaned")),
    }


def _read(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--product-report", required=True, type=Path)
    parser.add_argument("--general-report", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    report = build_four_flow_report(
        args.product_report,
        args.general_report,
    )
    rendered = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
