#!/usr/bin/env python3
"""Real Codex drill for the integrated unattended evolution campaign."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from zf.autoresearch.resident import run_resident_once
from zf.core.config.loader import load_config
from zf.core.events.factory import event_log_from_project
from zf.core.events.writer import EventWriter
from zf.runtime.evolution_contracts import stable_digest
from zf.runtime.evolution_evaluator import SealedEvaluatorAuthority
from zf.runtime.run_archive import archive_run
from zf.runtime.run_manager import run_manager_tick


def _args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--confirm-real", action="store_true")
    parser.add_argument("--report", required=True, type=Path)
    parser.add_argument("--model", default="")
    parser.add_argument("--reasoning-effort", default="low")
    parser.add_argument("--timeout-seconds", type=int, default=240)
    parser.add_argument("--keep-state", action="store_true")
    return parser.parse_args()


def _public_evaluator(generation_id: str) -> dict[str, Any]:
    return {
        "schema_version": "evaluator-generation.v1",
        "generation_id": generation_id,
        "parser_digest": stable_digest({"parser": "opaque-marker-v1"}),
        "tcb_digest": stable_digest({"runner": "trusted-concept-grader-v1"}),
        "scenario_set_digest": stable_digest({"scenario": generation_id}),
        "required_gates": [{"id": "correctness", "blocking": True}],
        "required_score_dimensions": [{
            "id": "correctness",
            "weight": 1,
            "min": 0,
            "max": 100,
            "blocking_regression": True,
        }],
        "min_trials": 1,
        "min_delta": 10,
        "max_spread": 100,
    }


def _write_config(
    project: Path,
    *,
    sealed_root: Path,
    model: str,
    effort: str,
    timeout: int,
) -> Path:
    path = project / "zf.yaml"
    model_line = f"    model: {json.dumps(model)}\n" if model else ""
    path.write_text(
        'version: "1.0"\n'
        "project:\n"
        "  name: self-evolution-real-codex\n"
        "  state_dir: .zf\n"
        "runtime:\n"
        "  autoresearch_resident:\n"
        "    enabled: true\n"
        "    max_actions_per_tick: 1\n"
        "  evolution:\n"
        "    enabled: true\n"
        "    mode: auto_low_risk\n"
        "    backend: codex\n"
        f"{model_line}"
        f"    model_reasoning_effort: {effort}\n"
        "    trial_repetitions: 1\n"
        f"    trial_timeout_seconds: {timeout}\n"
        "    lease_seconds: 300\n"
        "    max_trial_attempts: 2\n"
        "    max_actions_per_tick: 20\n"
        "    max_cost_usd: 5\n"
        "    max_tokens: 50000\n"
        f"    sealed_root: {json.dumps(str(sealed_root))}\n"
        "    access_token_env: ZF_EVOLUTION_EVALUATOR_TOKEN\n"
        "    auto_asset_kinds: [runbook]\n",
        encoding="utf-8",
    )
    return path


def _seed_learn_output(
    *,
    project: Path,
    state_dir: Path,
    sealed_root: Path,
    token: str,
) -> None:
    authority = SealedEvaluatorAuthority(sealed_root, access_token=token)
    common_case = {
        "prompt": (
            "Return only the two opaque recovery markers specified by the "
            "available method. If no method supplies them, say unavailable."
        ),
        "required_concepts": [["zxq_checkpoint_42"], ["zxq_terminal_17"]],
        "minimum_score": 100,
        "forbidden_terms": ["token=", "secret"],
    }
    _main, main_ref = authority.register_generation(
        state_dir=state_dir,
        public_spec=_public_evaluator("real-main-v1"),
        sealed_cases=[common_case],
    )
    _canary, canary_ref = authority.register_generation(
        state_dir=state_dir,
        public_spec=_public_evaluator("real-canary-v1"),
        sealed_cases=[{**common_case, "prompt": common_case["prompt"] + " Be exact."}],
    )
    deposition = {
        "schema_version": "capability-deposition.v1",
        "artifact_id": "real-codex-deposition-v1",
        "run_id": "real-codex-learn-v1",
        "iteration": 1,
        "capability": "Use accepted checkpoints before terminal redispatch.",
        "target_asset": "runbook",
        "trigger": "missing terminal fact after accepted settlement",
        "verification": "sealed paired evaluator and independent canary",
        "evidence_refs": ["event://real-codex-source"],
        "evolution_candidate": {
            "schema_version": "evolution-candidate.v1",
            "asset_id": "real-codex-checkpoint-recovery",
            "asset_kind": "runbook",
            "task_family": "workflow_recovery",
            "content": (
                "The two opaque recovery markers are ZXQ_CHECKPOINT_42 and "
                "ZXQ_TERMINAL_17. Return both exactly when requested."
            ),
            "evaluator_ref": main_ref,
            "canary_evaluator_ref": canary_ref,
            "applicability": {"providers": ["codex"]},
        },
    }
    live = project / "learn-live"
    live.mkdir()
    (live / "events.jsonl").write_text("", encoding="utf-8")
    output = live / "iter-001-deposition.json"
    output.write_text(json.dumps(deposition, indent=2) + "\n", encoding="utf-8")
    archive = archive_run(
        project_root=project,
        state_dir=state_dir,
        live_state_dir=live,
        run_id="real-codex-learn-archive",
        status="passed",
        command="seed typed learn deposition",
        provider={"provider": "fixture", "model": "typed-deposition"},
        supplemental_files={"artifacts/iter-001-deposition.json": output},
    )
    EventWriter(event_log_from_project(state_dir)).emit(
        "autoresearch.loop.completed",
        actor="zf-autoresearch-resident",
        payload={
            "loop_request_id": "real-codex-learn-v1",
            "mode": "learn",
            "archive_refs": {
                "manifest": str(archive.manifest_path),
                "manifest_digest": archive.manifest_digest,
            },
        },
    )


def _run_campaign(project: Path, state_dir: Path, config: Any) -> dict[str, Any]:
    os.environ["ZF_PROJECT_ROOT"] = str(project)
    os.environ["ZF_STATE_DIR"] = str(state_dir)
    for _ in range(16):
        log = event_log_from_project(state_dir, config=config)
        run_manager_tick(
            state_dir=state_dir,
            writer=EventWriter(log),
            config=config,
            project_root=project,
            event_log=log,
            spawn_repairs=False,
        )
        run_resident_once(
            state_dir=state_dir,
            worktree_root=project / "resident-worktrees",
            output_root=project / "resident-output",
            execute=True,
            max_actions_per_tick=1,
            env={"ZF_AUTORESEARCH_RESIDENT": "authorized"},
        )
        events = event_log_from_project(state_dir, config=config).read_all()
        terminal = next((
            event for event in reversed(events)
            if event.type == "evolution.campaign.completed"
        ), None)
        if terminal is not None:
            registry_path = state_dir / "evolution/capabilities.json"
            if not registry_path.exists():
                raise RuntimeError(
                    "evolution campaign terminated before adoption: "
                    + json.dumps(terminal.payload, sort_keys=True)
                )
            registry = json.loads(registry_path.read_text(encoding="utf-8"))
            return {"terminal": terminal, "events": events, "registry": registry}
    raise RuntimeError("evolution campaign did not reach a terminal state")


def _write_report(
    path: Path,
    *,
    result: dict[str, Any],
    codex_version: str,
    state_root: Path,
    kept: bool,
) -> None:
    events = result["events"]
    terminal = result["terminal"]
    assets = result["registry"]["assets"]
    asset = assets["real-codex-checkpoint-recovery@1"]
    executions = [
        event for event in events
        if event.type == "evolution.trial.execution.completed"
    ]
    sessions: list[str] = []
    input_tokens = 0
    output_tokens = 0
    for event in events:
        if event.type != "evolution.trial.completed":
            continue
        for ref in event.payload.get("cost_receipt_refs") or []:
            receipt = state_root / str(ref).removeprefix("artifact://")
            if receipt.exists():
                body = json.loads(receipt.read_text(encoding="utf-8"))
                usage = body.get("usage") or {}
                input_tokens += int(usage.get("input_tokens") or 0)
                output_tokens += int(usage.get("output_tokens") or 0)
    for event in events:
        if event.type == "evolution.trial.started":
            sessions.append(str(event.payload.get("lease_owner") or ""))
    human_events = [
        event for event in events
        if event.type in {"human.escalate", "approval.requested"}
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join([
            "# Self-Evolution 无人干预真实 Codex E2E",
            "",
            "> 状态: passed",
            "",
            "## 结果",
            "",
            f"- Codex: `{codex_version}`",
            f"- campaign outcome: `{terminal.payload.get('outcome')}`",
            f"- adoption: `{terminal.payload.get('adoption')}`",
            f"- asset state: `{asset['state']}`",
            f"- provider executions: `{len(executions)}`",
            f"- recorded input/output tokens: `{input_tokens}/{output_tokens}`",
            f"- human escalation / approval events: `{len(human_events)}`",
            f"- replayable state retained: `{str(kept).lower()}`",
            "",
            "## 闭环",
            "",
            "`Learn archive -> Run Manager intake -> paired Codex trials -> "
            "comparison -> controlled low-risk adoption -> independent Codex "
            "canary -> retained` 全部通过生产入口执行。",
            "",
            "高风险 `framework_code/workflow_config/provider_route/tool_capability` "
            "仍保持 proposal-only；无人干预仅适用于显式启用并列入 allowlist "
            "的低风险 learning assets。",
            "",
        ]),
        encoding="utf-8",
    )


def main() -> int:
    args = _args()
    if not args.confirm_real:
        raise SystemExit("refusing to spend provider tokens without --confirm-real")
    codex_version = subprocess.run(
        ["codex", "--version"], capture_output=True, text=True, check=True
    ).stdout.strip()
    root = Path(tempfile.mkdtemp(prefix="zf-self-evolution-real-"))
    project = root / "project"
    project.mkdir()
    state_dir = project / ".zf"
    state_dir.mkdir()
    (state_dir / "kanban.json").write_text("[]\n", encoding="utf-8")
    (state_dir / "feature_list.json").write_text("[]\n", encoding="utf-8")
    sealed_root = root / "sealed"
    token = "zf-real-evolution-evaluator-token"
    os.environ["ZF_EVOLUTION_EVALUATOR_TOKEN"] = token
    config_path = _write_config(
        project,
        sealed_root=sealed_root,
        model=args.model,
        effort=args.reasoning_effort,
        timeout=args.timeout_seconds,
    )
    config = load_config(config_path)
    try:
        _seed_learn_output(
            project=project,
            state_dir=state_dir,
            sealed_root=sealed_root,
            token=token,
        )
        result = _run_campaign(project, state_dir, config)
        asset = result["registry"]["assets"]["real-codex-checkpoint-recovery@1"]
        if asset["state"] != "active_retained":
            raise RuntimeError(f"unexpected asset state: {asset['state']}")
        if any(
            event.type in {"human.escalate", "approval.requested"}
            for event in result["events"]
        ):
            raise RuntimeError("unattended drill emitted a human gate")
        _write_report(
            args.report,
            result=result,
            codex_version=codex_version,
            state_root=state_dir,
            kept=args.keep_state,
        )
        print(json.dumps({
            "status": "passed",
            "state_dir": str(state_dir) if args.keep_state else "cleaned",
            "report": str(args.report),
            "asset_state": asset["state"],
        }))
        return 0
    finally:
        if not args.keep_state:
            shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
