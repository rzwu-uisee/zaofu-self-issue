#!/usr/bin/env bash
set -euo pipefail

# Real-provider smoke for examples/prod/new.
#
# This script intentionally consumes provider tokens. It creates a tiny Node.js
# product under /tmp, then runs PRD -> issue -> refactor with the production
# templates in examples/prod/new. It records a compact report and stops tmux
# sessions after each workflow.

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
source "$ROOT/tests/e2e/scripts/process_tree_cleanup.sh"
ZF_BIN="${ZF_BIN:-uv --project "$ROOT" run zf}"
BACKEND="${ZF_AGENT_BACKEND:-codex}"
RUN_MANAGER_BACKEND="${ZF_RUN_MANAGER_BACKEND:-$BACKEND}"
STAMP="${ZF_E2E_RUN_TAG:-$(date -u +%Y%m%dT%H%M%SZ)}"
RUN_ROOT="${ZF_E2E_ROOT:-/tmp/zf-prod-new-three-workflow-$STAMP}"
PRODUCT="$RUN_ROOT/product"
REPORT="$RUN_ROOT/report.json"
TIMEOUT_SECONDS="${ZF_E2E_TIMEOUT_SECONDS:-3600}"
TEMPLATE_FAMILY="${ZF_E2E_TEMPLATE_FAMILY:-controller-v3}"
FLOW_SET="${ZF_E2E_FLOW_SET:-prd,issue,refactor}"
REASONING_EFFORT="${ZF_E2E_REASONING_EFFORT:-low}"
MODEL="${ZF_E2E_MODEL:-}"
ROLE_TRANSPORT="${ZF_E2E_ROLE_TRANSPORT:-}"
TRANSPORT_TIMEOUT_SECONDS="${ZF_E2E_TRANSPORT_TIMEOUT_SECONDS:-600}"
OA_PLAN_POLICY="${ZF_E2E_OA_PLAN_POLICY:-}"
PREFLIGHT_ONLY="${ZF_E2E_PREFLIGHT_ONLY:-false}"
GLOBAL_BUDGET_USD="${ZF_GLOBAL_BUDGET_USD:-900}"
OPERATION_TIMEOUT_SECONDS="${ZF_E2E_OPERATION_TIMEOUT_SECONDS:-}"
OPERATION_TOKEN_BUDGET="${ZF_E2E_OPERATION_TOKEN_BUDGET:-}"
RUN_TIMEOUT_SECONDS="${ZF_E2E_RUN_TIMEOUT_SECONDS:-}"
RUN_TOKEN_BUDGET="${ZF_E2E_RUN_TOKEN_BUDGET:-}"
RUN_COST_BUDGET_USD="${ZF_E2E_RUN_COST_BUDGET_USD:-}"
TASK_PIPELINE_MODE="${ZF_E2E_TASK_PIPELINE_MODE:-${ZF_TASK_PIPELINE_MODE:-}}"
BASELINE_MANIFEST=""
ACTIVE_WATCHER_PID=""
ZF_SOURCE_COMMIT="$(git -C "$ROOT" rev-parse HEAD)"
ZF_SOURCE_STATUS="$(git -C "$ROOT" status --porcelain=v1)"
ZF_SOURCE_CLEAN=true
if [[ -n "$ZF_SOURCE_STATUS" ]]; then
  ZF_SOURCE_CLEAN=false
fi

if [[ -n "$ZF_SOURCE_STATUS" && "${ZF_E2E_ALLOW_DIRTY_SOURCE:-false}" != "true" ]]; then
  echo "real-provider E2E requires a clean ZaoFu checkout; set ZF_E2E_ALLOW_DIRTY_SOURCE=true only for diagnosis" >&2
  exit 2
fi
if [[ -n "$OA_PLAN_POLICY" && "$OA_PLAN_POLICY" != "shadow" && "$OA_PLAN_POLICY" != "blocking" ]]; then
  echo "ZF_E2E_OA_PLAN_POLICY must be shadow or blocking" >&2
  exit 2
fi
if [[ -n "$ROLE_TRANSPORT" && "$ROLE_TRANSPORT" != "tmux" && "$ROLE_TRANSPORT" != "stream-json" ]]; then
  echo "ZF_E2E_ROLE_TRANSPORT must be tmux or stream-json" >&2
  exit 2
fi
if [[ -n "$TASK_PIPELINE_MODE" && "$TASK_PIPELINE_MODE" != "shadow" && "$TASK_PIPELINE_MODE" != "blocking" ]]; then
  echo "ZF_E2E_TASK_PIPELINE_MODE must be shadow or blocking" >&2
  exit 2
fi
if [[ -n "$OPERATION_TOKEN_BUDGET" ]] && {
  [[ ! "$OPERATION_TOKEN_BUDGET" =~ ^[0-9]+$ ]] \
    || (( OPERATION_TOKEN_BUDGET < 1 || OPERATION_TOKEN_BUDGET > 10000000 ));
}; then
  echo "ZF_E2E_OPERATION_TOKEN_BUDGET must be an integer between 1 and 10000000" >&2
  exit 2
fi
if [[ -n "$OPERATION_TIMEOUT_SECONDS" ]] && {
  [[ ! "$OPERATION_TIMEOUT_SECONDS" =~ ^[0-9]+$ ]] \
    || (( OPERATION_TIMEOUT_SECONDS < 1 || OPERATION_TIMEOUT_SECONDS > 86400 ));
}; then
  echo "ZF_E2E_OPERATION_TIMEOUT_SECONDS must be an integer between 1 and 86400" >&2
  exit 2
fi
if [[ -n "$RUN_TIMEOUT_SECONDS" ]] && {
  [[ ! "$RUN_TIMEOUT_SECONDS" =~ ^[0-9]+$ ]] \
    || (( RUN_TIMEOUT_SECONDS < 1 || RUN_TIMEOUT_SECONDS > 86400 ));
}; then
  echo "ZF_E2E_RUN_TIMEOUT_SECONDS must be an integer between 1 and 86400" >&2
  exit 2
fi
if [[ -n "$RUN_TOKEN_BUDGET" ]] && {
  [[ ! "$RUN_TOKEN_BUDGET" =~ ^[0-9]+$ ]] \
    || (( RUN_TOKEN_BUDGET < 1 || RUN_TOKEN_BUDGET > 100000000 ));
}; then
  echo "ZF_E2E_RUN_TOKEN_BUDGET must be an integer between 1 and 100000000" >&2
  exit 2
fi
if [[ -n "$RUN_COST_BUDGET_USD" ]] && ! python3 - "$RUN_COST_BUDGET_USD" <<'PY'
import sys

try:
    value = float(sys.argv[1])
except ValueError:
    raise SystemExit(1)
raise SystemExit(0 if 0 < value <= 10_000 else 1)
PY
then
  echo "ZF_E2E_RUN_COST_BUDGET_USD must be a number greater than 0 and at most 10000" >&2
  exit 2
fi

case "$TEMPLATE_FAMILY" in
  controller-v3)
    PRD_TEMPLATE="$ROOT/examples/prod/controller/prd-fanout-v3.yaml"
    ISSUE_TEMPLATE="$ROOT/examples/prod/controller/issue-fanout-v3.yaml"
    REFACTOR_TEMPLATE="$ROOT/examples/prod/controller/refactor-lane-v3.yaml"
    ;;
  controller-v3-claude)
    PRD_TEMPLATE="$ROOT/examples/prod/controller/prd-fanout-v3-claude.yaml"
    ISSUE_TEMPLATE="$ROOT/examples/prod/controller/issue-fanout-v3-claude.yaml"
    REFACTOR_TEMPLATE="$ROOT/examples/prod/controller/refactor-lane-v3-claude.yaml"
    ;;
  controller-v4)
    PRD_TEMPLATE="$ROOT/examples/prod/controller/prd-task-pipeline-v4-canary.yaml"
    ISSUE_TEMPLATE="$ROOT/examples/prod/controller/issue-task-pipeline-v4-canary.yaml"
    REFACTOR_TEMPLATE="$ROOT/examples/prod/controller/refactor-task-pipeline-v4-canary.yaml"
    ;;
  controller-v4-claude)
    PRD_TEMPLATE="$ROOT/examples/prod/controller/prd-task-pipeline-v4-canary-claude.yaml"
    ISSUE_TEMPLATE="$ROOT/examples/prod/controller/issue-task-pipeline-v4-canary-claude.yaml"
    REFACTOR_TEMPLATE="$ROOT/examples/prod/controller/refactor-task-pipeline-v4-canary-claude.yaml"
    ;;
  legacy-v2)
    PRD_TEMPLATE="$ROOT/examples/prod/new/prd-fanout-v2.yaml"
    ISSUE_TEMPLATE="$ROOT/examples/prod/new/issue-fanout-v2.yaml"
    REFACTOR_TEMPLATE="$ROOT/examples/prod/new/refactor-lane-v2.yaml"
    ;;
  *)
    echo "unknown ZF_E2E_TEMPLATE_FAMILY: $TEMPLATE_FAMILY (expected controller-v3, controller-v3-claude, controller-v4, controller-v4-claude, or legacy-v2)" >&2
    exit 2
    ;;
esac

if [[ -n "$TASK_PIPELINE_MODE" ]]; then
  export ZF_TASK_PIPELINE_MODE="$TASK_PIPELINE_MODE"
fi

mkdir -p "$PRODUCT"
cd "$PRODUCT"

if [[ ! -d .git ]]; then
  git init -q
  git config user.email "zaofu-e2e@example.com"
  git config user.name "ZaoFu E2E"
  cat > README.md <<'MD'
# Product Pulse E2E Seed

Minimal dependency-free Node.js baseline for workflow verification.
MD
  cat > package.json <<'JSON'
{"scripts":{"test":"node --test"},"dependencies":{},"devDependencies":{}}
JSON
  cat > server.mjs <<'JS'
export function health() {
  return { ok: true, service: "product-pulse-seed", version: "0.0.0" };
}
JS
  mkdir -p src tests
  : > src/.gitkeep
  cat > tests/server.test.mjs <<'JS'
import test from "node:test";
import assert from "node:assert/strict";
import { health } from "../server.mjs";

test("seed health", () => {
  assert.equal(health().ok, true);
});
JS
  git add -- README.md package.json server.mjs src/.gitkeep tests/server.test.mjs
  GIT_AUTHOR_DATE="2026-08-02T00:00:00Z" \
    GIT_COMMITTER_DATE="2026-08-02T00:00:00Z" \
    git commit -q -m "chore: seed product pulse e2e baseline"
fi

seed_single_flow_baseline() {
  local fixture=""
  case "$FLOW_SET" in
    issue)
      fixture="$ROOT/tests/e2e/fixtures/product-pulse/issue-baseline"
      ;;
    refactor)
      fixture="$ROOT/tests/e2e/fixtures/product-pulse/refactor-baseline"
      ;;
    *)
      return 0
      ;;
  esac
  if [[ -d app ]]; then
    return 0
  fi
  BASELINE_MANIFEST="$fixture/manifest.json"
  python3 - "$fixture" "$FLOW_SET" <<'PY'
import hashlib
import json
import pathlib
import sys

root = pathlib.Path(sys.argv[1])
flow_kind = sys.argv[2]
manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
if manifest.get("schema_version") != "product-pulse-golden-baseline.v1":
    raise SystemExit("unsupported Product Pulse golden baseline manifest")
if manifest.get("flow_kind") != flow_kind:
    raise SystemExit("Product Pulse golden baseline flow mismatch")
expected = manifest.get("git_blobs")
if not isinstance(expected, dict) or not expected:
    raise SystemExit("Product Pulse golden baseline has no blob inventory")
actual_paths = {
    path.relative_to(root).as_posix()
    for path in root.joinpath("app").rglob("*")
    if path.is_file()
}
if actual_paths != set(expected):
    raise SystemExit("Product Pulse golden baseline file inventory drifted")
for relative, expected_digest in expected.items():
    body = root.joinpath(relative).read_bytes()
    header = f"blob {len(body)}\0".encode()
    actual = hashlib.sha1(header + body).hexdigest()
    if actual != expected_digest:
        raise SystemExit(f"Product Pulse golden baseline blob drifted: {relative}")
PY
  cp -R "$fixture/app" app
  git add -- app
  GIT_AUTHOR_DATE="2026-08-02T00:01:00Z" \
    GIT_COMMITTER_DATE="2026-08-02T00:01:00Z" \
    git commit -q -m "test: seed $FLOW_SET product pulse baseline"
}

seed_single_flow_baseline

latest_payload_field() {
  local state_dir="$1"
  local event_type="$2"
  local field="$3"
  python3 - "$state_dir/events.jsonl" "$event_type" "$field" <<'PY'
import json, sys
path, event_type, field = sys.argv[1], sys.argv[2], sys.argv[3]
value = ""
try:
    lines = open(path, encoding="utf-8")
except FileNotFoundError:
    print("")
    raise SystemExit
for line in lines:
    try:
        event = json.loads(line)
    except Exception:
        continue
    if event.get("type") != event_type:
        continue
    payload = event.get("payload") or {}
    value = str(payload.get(field) or "")
print(value)
PY
}

event_type_count() {
  local state_dir="$1"
  local event_type="$2"
  python3 - "$state_dir/events.jsonl" "$event_type" <<'PY'
import json, sys
path, event_type = sys.argv[1:]
count = 0
try:
    lines = open(path, encoding="utf-8")
except FileNotFoundError:
    lines = []
for line in lines:
    try:
        event = json.loads(line)
    except Exception:
        continue
    if event.get("type") == event_type:
        count += 1
print(count)
PY
}

wait_for_terminal_delivery() {
  local state_dir="$1"
  local audit_path="$2"
  local deadline=$((SECONDS + TIMEOUT_SECONDS))
  while (( SECONDS < deadline )); do
    if uv --project "$ROOT" run python \
      "$ROOT/tests/e2e/scripts/prod_flow_terminal_audit.py" \
      --state-dir "$state_dir" \
      --fail-on-human-escalate \
      --fail-on-repeated-child-failure \
      --output "$audit_path" \
      >"$audit_path.stdout"; then
      jq -r '.terminal.event_type' "$audit_path"
      return 0
    else
      local audit_status=$?
      if [[ "$audit_status" -ne 10 ]]; then
        cat "$audit_path" >&2
        return 1
      fi
    fi
    sleep 5
  done
  uv --project "$ROOT" run python \
    "$ROOT/tests/e2e/scripts/prod_flow_terminal_audit.py" \
    --state-dir "$state_dir" \
    --fail-on-human-escalate \
    --fail-on-repeated-child-failure \
    --output "$audit_path" \
    >"$audit_path.stdout" || true
  echo "timeout waiting for consistent terminal delivery in $state_dir" >&2
  return 1
}

append_report() {
  local name="$1"
  local state_dir="$2"
  local audit_path="$3"
  local context_audit_path="$4"
  local run_status="$5"
  local run_reason="$6"
  local failure_class="$7"
  local product_source_commit="$8"
  local product_head_commit="$9"
  local template="${10}"
  local payload_file="${11}"
  local started_epoch="${12}"
  python3 - \
    "$REPORT" "$name" "$state_dir" "$audit_path" \
    "$context_audit_path" "$run_status" "$run_reason" "$failure_class" \
    "$product_source_commit" "$product_head_commit" "$template" \
    "$payload_file" "$started_epoch" "$BACKEND" "$REASONING_EFFORT" \
    "${MODEL:-provider_default}" \
    "$ZF_SOURCE_COMMIT" "$ZF_SOURCE_CLEAN" "$OA_PLAN_POLICY" "$TIMEOUT_SECONDS" \
    "$GLOBAL_BUDGET_USD" "$BASELINE_MANIFEST" "$OPERATION_TIMEOUT_SECONDS" \
    "$OPERATION_TOKEN_BUDGET" \
    "$RUN_TIMEOUT_SECONDS" "$RUN_TOKEN_BUDGET" "$RUN_COST_BUDGET_USD" \
    "$TASK_PIPELINE_MODE" <<'PY'
import hashlib, json, pathlib, subprocess, sys, time
(
    report, name, state_dir, audit_path, context_audit_path, run_status,
    run_reason, failure_class, product_source_commit, product_head_commit,
    template, payload_file, started_epoch, backend, reasoning_effort, model,
    zaofu_source_commit, zaofu_source_clean, oa_plan_policy, timeout_seconds,
    global_budget_usd, baseline_manifest_path, operation_timeout_seconds,
    operation_token_budget,
    run_timeout_seconds, run_token_budget, run_cost_budget_usd,
    task_pipeline_mode,
) = sys.argv[1:]
events_path = f"{state_dir}/events.jsonl"
types = [
    "task_map.ready", "dev.build.done", "review.approved", "test.passed",
    "judge.passed", "run.completed", "run.goal.completed", "run.goal.blocked",
    "human.escalate",
    "workflow.resume.rejected", "autoresearch.trigger.accepted",
    "supervisor.decision.recorded",
    "orchestrator.semantic.decision.applied",
    "orchestrator.semantic.decision.observed",
    "orchestrator.semantic.checkpoint.skipped",
    "orchestrator.semantic.rework.requested", "task.rework.requested",
    "plan.rejected", "test.failed", "verify.passed", "verify.failed",
]
counts = {key: 0 for key in types}
attempts = {
    "workflow_operation_requested": 0,
    "workflow_operation_settled": 0,
    "workflow_operation_failed": 0,
    "task_attempt_started": 0,
    "task_attempt_succeeded": 0,
    "task_attempt_failed": 0,
}
usage_identities = {}
usage_context_windows = set()
try:
    lines = open(events_path, encoding="utf-8")
except FileNotFoundError:
    lines = []
for line in lines:
    try:
        event = json.loads(line)
    except Exception:
        continue
    typ = event.get("type")
    if typ in counts:
        counts[typ] += 1
    attempt_key = {
        "workflow.operation.requested": "workflow_operation_requested",
        "workflow.operation.settled": "workflow_operation_settled",
        "workflow.operation.failed": "workflow_operation_failed",
        "workflow.operation.blocked": "workflow_operation_failed",
        "task.attempt.started": "task_attempt_started",
        "task.attempt.succeeded": "task_attempt_succeeded",
        "task.attempt.failed": "task_attempt_failed",
    }.get(typ)
    if attempt_key:
        attempts[attempt_key] += 1
    if typ == "agent.usage":
        payload = event.get("payload") or {}
        actual_backend = str(payload.get("backend") or "")
        actual_model = str(payload.get("model") or "")
        actor = str(event.get("actor") or "")
        if actual_backend and actual_model and actor:
            identity = {
                "role_instance": actor,
                "model": actual_model,
                "comp_hash": "",
                "multi_agent_version": "",
                "reasoning_effort": "",
            }
            usage_identities[json.dumps(identity, sort_keys=True)] = identity
        window = payload.get("model_context_window")
        if isinstance(window, (int, float)) and int(window) > 0:
            usage_context_windows.add(int(window))
try:
    data = json.load(open(report, encoding="utf-8"))
except Exception:
    data = {"schema_version": "prod-new-three-workflow-e2e.v2", "runs": []}
data["schema_version"] = "prod-new-three-workflow-e2e.v2"
try:
    terminal_audit = json.load(open(audit_path, encoding="utf-8"))
except Exception:
    terminal_audit = {
        "schema_version": "prod-flow-terminal-delivery-audit.v1",
        "status": "missing",
        "reason": "terminal audit was not written",
    }
try:
    context_audit = json.load(open(context_audit_path, encoding="utf-8"))
except Exception:
    context_audit = {
        "schema_version": "prod-flow-context-audit.v1",
        "status": "missing",
        "reasons": ["context audit was not written"],
    }
template_bytes = pathlib.Path(template).read_bytes()
config_path = pathlib.Path(state_dir).parent / "zf.yaml"
config_bytes = config_path.read_bytes() if config_path.is_file() else b""
prompt_bytes = pathlib.Path(payload_file).read_bytes()
product_root = pathlib.Path(state_dir).parent
try:
    product_source_tree = subprocess.run(
        ["git", "-C", str(product_root), "rev-parse", f"{product_source_commit}^{{tree}}"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
except (OSError, subprocess.CalledProcessError):
    product_source_tree = ""
baseline_manifest = {}
baseline_manifest_sha256 = ""
if baseline_manifest_path:
    try:
        baseline_bytes = pathlib.Path(baseline_manifest_path).read_bytes()
        baseline_manifest = json.loads(baseline_bytes)
        baseline_manifest_sha256 = hashlib.sha256(baseline_bytes).hexdigest()
    except (OSError, json.JSONDecodeError):
        baseline_manifest = {"status": "unreadable"}

roles = {}
workdirs = pathlib.Path(state_dir) / "workdirs"
for session_path in sorted(workdirs.glob("*/codex-home/sessions/**/*.jsonl")):
    role = session_path.relative_to(workdirs).parts[0]
    try:
        rows = session_path.open(encoding="utf-8")
    except OSError:
        continue
    for line in rows:
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if row.get("type") != "turn_context":
            continue
        payload = row.get("payload") or {}
        identity = {
            "role_instance": role,
            "model": str(payload.get("model") or ""),
            "comp_hash": str(payload.get("comp_hash") or ""),
            "multi_agent_version": str(payload.get("multi_agent_version") or ""),
            "reasoning_effort": str(payload.get("effort") or ""),
        }
        key = json.dumps(identity, sort_keys=True)
        roles[key] = identity
        break
for key, identity in usage_identities.items():
    roles.setdefault(key, identity)
actual_roles = sorted(roles.values(), key=lambda row: (
    row["role_instance"], row["model"], row["comp_hash"],
    row["multi_agent_version"], row["reasoning_effort"],
))
provider_actual = {
    "status": "ready" if actual_roles else "missing",
    "roles": actual_roles,
    "models": sorted({row["model"] for row in actual_roles if row["model"]}),
    "comp_hashes": sorted({row["comp_hash"] for row in actual_roles if row["comp_hash"]}),
    "multi_agent_versions": sorted({
        row["multi_agent_version"] for row in actual_roles
        if row["multi_agent_version"]
    }),
    "reasoning_efforts": sorted({
        row["reasoning_effort"] for row in actual_roles
        if row["reasoning_effort"]
    }),
    "context_windows": sorted(usage_context_windows),
}
passed = all((
    run_status == "passed",
    terminal_audit.get("status") == "passed",
    context_audit.get("status") == "passed",
))
data["runs"].append({
    "name": name,
    "state_dir": state_dir,
    "source_identity": {
        "zaofu_commit": zaofu_source_commit,
        "zaofu_clean": zaofu_source_clean == "true",
        "product_source_commit": product_source_commit,
        "product_source_tree": product_source_tree,
        "product_head_commit": product_head_commit,
        "baseline_manifest": baseline_manifest,
        "baseline_manifest_sha256": baseline_manifest_sha256,
    },
    "prompt_sha256": hashlib.sha256(prompt_bytes).hexdigest(),
    "config": {
        "template": template,
        "template_sha256": hashlib.sha256(template_bytes).hexdigest(),
        "rendered_sha256": hashlib.sha256(config_bytes).hexdigest(),
        "oa_plan_policy_override": oa_plan_policy,
        "task_pipeline_mode_override": task_pipeline_mode,
    },
    "provider": {
        "backend": backend,
        "model": model,
        "reasoning_effort": reasoning_effort,
        "actual_identity": provider_actual,
    },
    "budget": {
        "timeout_seconds": int(timeout_seconds),
        "global_cost_budget_usd": float(global_budget_usd),
        "operation_timeout_seconds_override": (
            int(operation_timeout_seconds) if operation_timeout_seconds else None
        ),
        "operation_token_budget_override": (
            int(operation_token_budget) if operation_token_budget else None
        ),
        "outer_timeout_seconds": int(timeout_seconds),
        "provider_global_cost_budget_usd": float(global_budget_usd),
        "run_limits_override": {
            "timeout_seconds": (
                int(run_timeout_seconds) if run_timeout_seconds else None
            ),
            "token_budget": int(run_token_budget) if run_token_budget else None,
            "cost_budget_usd": (
                float(run_cost_budget_usd) if run_cost_budget_usd else None
            ),
        },
        "operation_limits_override": {
            "timeout_seconds": (
                int(operation_timeout_seconds) if operation_timeout_seconds else None
            ),
            "token_budget": (
                int(operation_token_budget) if operation_token_budget else None
            ),
        },
    },
    "terminal": terminal_audit.get("terminal", {}).get("event_type", ""),
    "status": "passed" if passed else "failed",
    "reason": run_reason,
    "failure_classification": "none" if passed else failure_class,
    "duration_seconds": max(0, int(time.time()) - int(started_epoch)),
    "terminal_delivery": terminal_audit,
    "context_handoff": context_audit,
    "usage": context_audit.get("usage", {}),
    "oa_metrics": context_audit.get("oa", {}).get("metrics", {}),
    "attempts": attempts,
    "counts": counts,
})
open(report, "w", encoding="utf-8").write(json.dumps(data, ensure_ascii=False, indent=2) + "\n")
PY
}

flow_enabled() {
  [[ ",$FLOW_SET," == *",$1,"* ]]
}

prepare_refactor_source_snapshot() {
  local source_commit
  local snapshot
  source_commit="$(git rev-parse HEAD)"
  snapshot="$RUN_ROOT/refactor-source-${source_commit:0:12}"
  if [[ ! -d "$snapshot" ]]; then
    mkdir -p "$snapshot"
    git archive "${source_commit}:app" | tar -x -C "$snapshot"
  fi
  printf '%s\n' "$snapshot"
}

render_runtime_config() {
  local name="$1"
  local yaml="$2"
  local state_name="$3"
  local session_name="$4"
  $ZF_BIN config render \
    --config "$yaml" \
    --output zf.yaml \
    >"$RUN_ROOT/$name-config-render.json"
  uv --project "$ROOT" run python - \
    zf.yaml "$ROOT" "$state_name" "$session_name" "$name" \
    "$REASONING_EFFORT" "$OA_PLAN_POLICY" "$BACKEND" \
    "$RUN_MANAGER_BACKEND" "$MODEL" "$ROLE_TRANSPORT" \
    "$TRANSPORT_TIMEOUT_SECONDS" "$OPERATION_TIMEOUT_SECONDS" \
    "$OPERATION_TOKEN_BUDGET" \
    "$RUN_TIMEOUT_SECONDS" "$RUN_TOKEN_BUDGET" "$RUN_COST_BUDGET_USD" \
    "$TASK_PIPELINE_MODE" <<'PY'
from pathlib import Path
import os
import sys

import yaml

(
    path, root, state_name, session_name, name, reasoning_effort,
    oa_plan_policy, backend, run_manager_backend, model, role_transport,
    transport_timeout_seconds, operation_timeout_seconds,
    operation_token_budget, run_timeout_seconds, run_token_budget,
    run_cost_budget_usd, task_pipeline_mode,
) = sys.argv[1:]
config_path = Path(path)
config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
if task_pipeline_mode:
    task_pipeline = (
        config.get("workflow", {})
        .get("_flow_metadata", {})
        .get("task_pipeline")
    )
    actual_mode = (
        str(task_pipeline.get("mode") or "")
        if isinstance(task_pipeline, dict)
        else ""
    )
    if actual_mode != task_pipeline_mode:
        raise SystemExit(
            "rendered Task Pipeline mode mismatch: "
            f"expected {task_pipeline_mode!r}, got {actual_mode!r}"
        )
config["project"]["name"] = f"prod-new-{name}"
config["project"]["state_dir"] = state_name
config["session"]["tmux_session"] = session_name
orchestrator = config.setdefault("orchestrator", {})
orchestrator["backend"] = backend
orchestrator["transport_timeout_s"] = float(transport_timeout_seconds)
if model:
    orchestrator["model"] = model
for role in config.get("roles") or []:
    if role.get("backend") == backend:
        role["model_reasoning_effort"] = reasoning_effort
        if backend == "claude-code":
            provider_session = role.get("provider_session")
            if not isinstance(provider_session, dict):
                provider_session = {}
                role["provider_session"] = provider_session
            provider_session["effort"] = reasoning_effort
        if model:
            role["model"] = model
        if role_transport:
            role["transport"] = role_transport
for source in config.get("skill_sources") or []:
    if source.get("name") == "zaofu-skills":
        source["path"] = str(Path(root) / "skills")
run_manager = config.setdefault("runtime", {}).setdefault("run_manager", {})
run_manager["backend"] = run_manager_backend
resident = run_manager.setdefault("resident_agent", {})
resident["tmux_session"] = f"{session_name}-run-manager"
resident["model_reasoning_effort"] = reasoning_effort
resident["enabled"] = (
    os.environ.get("ZF_RUN_MANAGER_RESIDENT_ENABLED", "true").lower()
    == "true"
)
if model:
    resident["model"] = model
if operation_timeout_seconds or operation_token_budget:
    profiles = config.setdefault("workflow", {}).setdefault(
        "execution_profiles", {}
    )
    bounded = profiles.get("bounded-direct-v1")
    if not isinstance(bounded, dict):
        raise SystemExit(
            "bounded-direct-v1 execution profile is unavailable for limit override"
        )
    limits = bounded.setdefault("limits", {})
    if operation_timeout_seconds:
        limits["timeout_seconds"] = int(operation_timeout_seconds)
    if operation_token_budget:
        limits["token_budget"] = int(operation_token_budget)
if run_timeout_seconds or run_token_budget or run_cost_budget_usd:
    run_limits = config.setdefault("workflow", {}).setdefault("run_limits", {})
    if run_timeout_seconds:
        run_limits["timeout_seconds"] = int(run_timeout_seconds)
    if run_token_budget:
        run_limits["token_budget"] = int(run_token_budget)
    if run_cost_budget_usd:
        run_limits["cost_budget_usd"] = float(run_cost_budget_usd)
if oa_plan_policy:
    orchestration = config.setdefault("workflow", {}).setdefault(
        "orchestration", {}
    )
    for key in (
        "checkpoints",
        "checkpoint_policies",
        "pilot_id",
        "shadow_sample_percent",
    ):
        orchestration.pop(key, None)
    orchestration["mode"] = "exception_advisor"
    flow_policy = {
        "mode": "semantic_control",
        "checkpoints": ["plan_candidate"],
        "checkpoint_policies": {"plan_candidate": oa_plan_policy},
        "shadow_sample_percent": 100,
    }
    if oa_plan_policy == "blocking":
        flow_policy["pilot_id"] = f"{name}-plan-candidate-full-e2e"
    orchestration.setdefault("flow_policies", {})[name] = flow_policy
config_path.write_text(
    yaml.safe_dump(config, sort_keys=False, allow_unicode=True),
    encoding="utf-8",
)
PY
}

wait_for_session() {
  local state_dir="$1"
  local watcher_pid="$2"
  local loop_started_before="$3"
  local start_log="$4"
  local deadline=$((SECONDS + 180))
  while (( SECONDS < deadline )); do
    if ! kill -0 "$watcher_pid" 2>/dev/null; then
      echo "zf start exited before session became ready" >&2
      cat "$start_log" >&2
      return 1
    fi
    if [[ -f "$state_dir/session.yaml" ]] \
      && (( $(event_type_count "$state_dir" loop.started) > loop_started_before )); then
      sleep 1
      if kill -0 "$watcher_pid" 2>/dev/null; then
        return 0
      fi
      echo "zf start exited during session readiness stabilization" >&2
      cat "$start_log" >&2
      return 1
    fi
    sleep 2
  done
  echo "timeout waiting for a new loop.started in $state_dir" >&2
  cat "$start_log" >&2
  return 1
}

stop_runtime() {
  local watcher_pid="$1"
  $ZF_BIN stop --include-run-manager --clean-workdirs || true
  if kill -0 "$watcher_pid" 2>/dev/null; then
    zf_e2e_terminate_process_tree "$watcher_pid"
  else
    wait "$watcher_pid" 2>/dev/null || true
  fi
  if [[ "${ACTIVE_WATCHER_PID:-}" == "$watcher_pid" ]]; then
    ACTIVE_WATCHER_PID=""
  fi
}

emit_run_simulation_done() {
  local state_dir="$1"
  local name="$2"
  local run_status="$3"
  local run_reason="$4"
  local failure_class="$5"
  local payload
  [[ -d "$state_dir" ]] || return 0
  payload="$(python3 - "$name" "$run_status" "$run_reason" "$failure_class" <<'PY'
import json
import sys

print(json.dumps({
    "purpose": "prod-new-three-workflow-e2e",
    "workflow_kind": sys.argv[1],
    "status": sys.argv[2],
    "reason": sys.argv[3],
    "failure_classification": sys.argv[4],
}, ensure_ascii=False))
PY
)"
  $ZF_BIN emit simulation.done \
    --actor prod-new-three-workflow-e2e \
    --payload "$payload" \
    --state-dir "$state_dir" >/dev/null 2>&1 || true
}

handle_runner_signal() {
  local exit_code="$1"
  trap - INT TERM
  if [[ -n "${ACTIVE_WATCHER_PID:-}" ]]; then
    stop_runtime "$ACTIVE_WATCHER_PID"
  elif [[ -f zf.yaml ]]; then
    $ZF_BIN stop --include-run-manager --clean-workdirs || true
  fi
  exit "$exit_code"
}

trap 'handle_runner_signal 130' INT
trap 'handle_runner_signal 143' TERM

if [[ "${ZF_E2E_SIGNAL_CLEANUP_SELFTEST:-false}" == "true" ]]; then
  sleep 300 &
  ACTIVE_WATCHER_PID="$!"
  if [[ -n "${ZF_E2E_SIGNAL_SELFTEST_PID_FILE:-}" ]]; then
    printf '%s\n' "$ACTIVE_WATCHER_PID" >"$ZF_E2E_SIGNAL_SELFTEST_PID_FILE"
  fi
  kill -TERM "$$"
  exit 99
fi

commit_initialized_instruction_baseline() {
  local instruction_status
  instruction_status="$(git status --porcelain=v1 -- AGENTS.md CLAUDE.md)"
  if [[ -n "$instruction_status" ]]; then
    git add -- AGENTS.md CLAUDE.md
    GIT_AUTHOR_DATE="2026-08-02T00:02:00Z" \
      GIT_COMMITTER_DATE="2026-08-02T00:02:00Z" \
      git commit -q -m "chore: record ZaoFu E2E instruction baseline"
  fi
  if [[ -n "$(git status --porcelain=v1 --untracked-files=no)" ]]; then
    echo "tracked files remain dirty after zf init" >&2
    git status --short --untracked-files=no >&2
    return 1
  fi
}

submit_flow() {
  local name="$1"
  local payload_file="$2"
  local objective
  local acceptance
  local source_root=""
  local target_root="app"
  local source_doc
  local source_relative
  local intake_doc="$PRODUCT/docs/intake/$name-$STAMP.md"
  objective="$(jq -r '.objective // .text // empty' "$payload_file")"
  acceptance="$(jq -r '.text // .objective // empty' "$payload_file")"
  case "$name" in
    prd)
      source_relative="docs/prd/TODO.md"
      ;;
    issue)
      source_relative="docs/issues/TODO.md"
      ;;
    refactor)
      source_relative="docs/plans/refactor-goal.md"
      ;;
    *)
      echo "unsupported workflow kind: $name" >&2
      return 2
      ;;
  esac
  source_doc="$PRODUCT/$source_relative"
  mkdir -p "$(dirname "$source_doc")" "$(dirname "$intake_doc")"
  printf '# %s workflow source\n\n%s\n' "$name" "$acceptance" >"$source_doc"
  git add -- "$source_relative"
  if ! git diff --cached --quiet -- "$source_relative"; then
    git commit -q -m "docs: add $name E2E input"
  fi
  if [[ "$name" == "refactor" ]]; then
    source_root="$(prepare_refactor_source_snapshot)"
  fi

  local intake_args=(
    flow intake
    --kind "$name"
    --from "$source_doc"
    --objective "$objective"
    --target "$target_root"
    --backend "$BACKEND"
    --project-id "prod-new-$name-$STAMP"
    --request-id "prod-new-$name-$STAMP"
    --acceptance "$acceptance"
    --output "$intake_doc"
    --json
  )
  if [[ -n "$source_root" ]]; then
    intake_args+=(--source-root "$source_root")
  fi
  $ZF_BIN "${intake_args[@]}" >"$RUN_ROOT/$name-flow-intake.json"
  $ZF_BIN flow clarify \
    --config zf.yaml \
    --intake "$intake_doc" \
    --confirm \
    --actor prod-new-e2e \
    --json \
    >"$RUN_ROOT/$name-flow-clarify.json"
  $ZF_BIN flow submit \
    --config zf.yaml \
    --intake "$intake_doc" \
    --kind "$name" \
    --requested-by prod-new-e2e \
    --reason "real provider $name workflow E2E" \
    --apply \
    --json \
    >"$RUN_ROOT/$name-flow-submit.json"
  jq -e '
    .status == "accepted"
    and (.workflow_invoke_status | IN(
      "pending_consumer", "accepted", "admitted", "queued", "already_requested"
    ))
  ' "$RUN_ROOT/$name-flow-submit.json" >/dev/null
}

run_workflow() {
  local name="$1"
  local yaml="$2"
  local state_name="$3"
  local session_name="$4"
  local payload_file="$5"
  local state_dir="$PRODUCT/$state_name"
  local source_commit
  local audit_path="$RUN_ROOT/$name-terminal-delivery.json"
  local context_audit_path="$RUN_ROOT/$name-context-handoff.json"
  local start_log="$RUN_ROOT/$name-zf-start.log"
  local started_epoch
  source_commit="$(git rev-parse HEAD)"
  started_epoch="$(date +%s)"
  record_run() {
    emit_run_simulation_done "$state_dir" "$name" "$1" "$2" "$3"
    append_report \
      "$name" "$state_dir" "$audit_path" "$context_audit_path" \
      "$1" "$2" "$3" "$source_commit" "$(git rev-parse HEAD)" \
      "$yaml" "$payload_file" "$started_epoch"
  }
  export ZF_PROJECT_NAME="prod-new-$name-$STAMP"
  export ZF_STATE_DIR="$state_dir"
  export ZF_TMUX_SESSION="$session_name"
  export ZF_AGENT_BACKEND="$BACKEND"
  export ZF_RUN_MANAGER_BACKEND="$RUN_MANAGER_BACKEND"
  export ZF_RUN_MANAGER_REFLECT_BACKEND="$RUN_MANAGER_BACKEND"
  export ZF_RUN_MANAGER_RESIDENT_ENABLED="${ZF_RUN_MANAGER_RESIDENT_ENABLED:-true}"
  export ZF_GLOBAL_BUDGET_USD="$GLOBAL_BUDGET_USD"
  if [[ "$BACKEND" == "claude-code" ]]; then
    export CLAUDE_CODE_EFFORT_LEVEL="$REASONING_EFFORT"
  fi
  if ! render_runtime_config "$name" "$yaml" "$state_name" "$session_name"; then
    record_run failed "runtime config render failed" config_render
    return 1
  fi
  if ! $ZF_BIN init; then
    record_run failed "zf init failed" runtime_init
    return 1
  fi
  if ! commit_initialized_instruction_baseline; then
    record_run failed "initialized instruction baseline is dirty" runtime_init
    return 1
  fi
  source_commit="$(git rev-parse HEAD)"
  if [[ "$PREFLIGHT_ONLY" == "true" ]]; then
    if ! $ZF_BIN validate --cold-start >"$RUN_ROOT/$name-cold-start.log"; then
      return 1
    fi
    if ! npm test; then
      return 1
    fi
    if [[ -f app/package.json ]] && ! (cd app && npm test); then
      return 1
    fi
    return 0
  fi
  local loop_started_before
  loop_started_before="$(event_type_count "$state_dir" loop.started)"
  $ZF_BIN start >"$start_log" 2>&1 &
  local watcher_pid=$!
  ACTIVE_WATCHER_PID="$watcher_pid"
  if ! wait_for_session \
    "$state_dir" "$watcher_pid" "$loop_started_before" "$start_log"; then
    record_run failed "runtime session did not become ready" runtime_start
    stop_runtime "$watcher_pid"
    return 1
  fi
  if ! submit_flow "$name" "$payload_file"; then
    record_run failed "workflow intake or submit failed" workflow_submit
    stop_runtime "$watcher_pid"
    return 1
  fi
  local terminal_event
  local terminal_event_path="$RUN_ROOT/$name-terminal-event.txt"
  if ! wait_for_terminal_delivery "$state_dir" "$audit_path" \
    >"$terminal_event_path"; then
    uv --project "$ROOT" run python \
      "$ROOT/tests/e2e/scripts/prod_flow_context_audit.py" \
      --state-dir "$state_dir" \
      --output "$context_audit_path" \
      >"$context_audit_path.stdout" || true
    record_run failed "terminal delivery closure failed" terminal_delivery
    stop_runtime "$watcher_pid"
    return 1
  fi
  terminal_event="$(<"$terminal_event_path")"
  if [[ "$terminal_event" != "run.goal.completed" ]]; then
    record_run failed "workflow ended at $terminal_event" workflow_terminal
    stop_runtime "$watcher_pid"
    echo "$name ended at $terminal_event" >&2
    return 1
  fi
  if ! uv --project "$ROOT" run python \
    "$ROOT/tests/e2e/scripts/prod_flow_context_audit.py" \
    --state-dir "$state_dir" \
    --output "$context_audit_path" \
    >"$context_audit_path.stdout"; then
    record_run failed "Plan to Judge context handoff audit failed" context_handoff
    stop_runtime "$watcher_pid"
    return 1
  fi
  local candidate
  candidate="$(latest_payload_field "$state_dir" candidate.ready candidate_ref)"
  if [[ -n "$candidate" ]]; then
    if ! git merge --ff-only "$candidate"; then
      record_run failed "candidate fast-forward merge failed" candidate_merge
      stop_runtime "$watcher_pid"
      return 1
    fi
  fi
  if ! npm test; then
    record_run failed "product npm test failed" product_verification
    stop_runtime "$watcher_pid"
    return 1
  fi
  if [[ -f app/package.json ]] && ! (cd app && npm test); then
    record_run failed "product npm test failed" product_verification
    stop_runtime "$watcher_pid"
    return 1
  fi
  record_run passed \
    "terminal delivery, context handoff, and product tests passed" none
  stop_runtime "$watcher_pid"
}

cat > "$RUN_ROOT/prd-request.json" <<JSON
{
  "text": "请基于当前 Node.js baseline 在 app/ 构建一个极小的 Product Pulse 产品。已确认产品合同: 1) app/package.json 版本为 1.0.0,不使用外部依赖; 2) /health 精确返回 {ok:true, service:'product-pulse', version:'1.0.0'}; 3) /api/pulse 使用固定内存数据并按 updatedAt 降序返回三条: pulse-003/Search healthy/2026-08-01T12:00:00Z, pulse-002/Checkout warning/2026-08-01T11:00:00Z, pulse-001/Catalog degraded/2026-08-01T10:00:00Z,每条字段为 id/title/status/updatedAt; 4) 首页服务端渲染 Product Pulse 标题及同一数据源的三条 title/status; 5) 服务支持 PORT,测试使用临时端口启动真实 HTTP listener 并在结束时关闭; 6) 保留 npm test,覆盖 /health、/api/pulse、排序、首页标题和三条状态渲染。不要引入外部依赖。请走完整 PRD->task_map->impl->verify->judge 流程,产出可运行产品。",
  "objective": "build minimal Product Pulse product from PRD to production-ready candidate",
  "run_tag": "prod-new-prd-$STAMP",
  "source_commit": "$(git rev-parse HEAD)"
}
JSON
overall_rc=0
if flow_enabled prd; then
  if ! run_workflow prd "$PRD_TEMPLATE" ".zf-prod-new-prd-$STAMP" "zf-prod-new-prd-$STAMP" "$RUN_ROOT/prd-request.json"; then
    overall_rc=1
  fi
fi

cat > "$RUN_ROOT/issue-request.json" <<JSON
{
  "text": "Issue: /api/pulse currently ignores query parameters. Add support for GET /api/pulse?status=<status> so it returns only items with an exact matching status while the default /api/pulse still returns all three items newest-first. Add node:test coverage for filtered and unmatched status behavior. Keep no external dependencies and preserve existing Product Pulse behavior.",
  "objective": "fix Product Pulse API status filtering regression with tests",
  "run_tag": "prod-new-issue-$STAMP",
  "source_commit": "$(git rev-parse HEAD)"
}
JSON
if flow_enabled issue; then
  if ! run_workflow issue "$ISSUE_TEMPLATE" ".zf-prod-new-issue-$STAMP" "zf-prod-new-issue-$STAMP" "$RUN_ROOT/issue-request.json"; then
    overall_rc=1
  fi
fi

cat > "$RUN_ROOT/refactor-request.json" <<JSON
{
  "pdd_id": "prod-new-refactor-$STAMP",
  "feature_id": "product-pulse-server-structure",
  "target_ref": "HEAD",
  "source_commit": "$(git rev-parse HEAD)",
  "run_tag": "prod-new-refactor-$STAMP",
  "objective": "Refactor Product Pulse server internals without changing behavior. Separate pulse data access/filtering and HTML rendering into small pure functions inside the existing no-dependency Node.js project. Preserve /health, /api/pulse, /api/pulse?status=<status>, homepage rendering, package metadata, and npm test results. Keep changes small and covered by node:test."
}
JSON
if flow_enabled refactor; then
  if ! run_workflow refactor "$REFACTOR_TEMPLATE" ".zf-prod-new-refactor-$STAMP" "zf-prod-new-refactor-$STAMP" "$RUN_ROOT/refactor-request.json"; then
    overall_rc=1
  fi
fi

echo "report: $REPORT"
exit "$overall_rc"
