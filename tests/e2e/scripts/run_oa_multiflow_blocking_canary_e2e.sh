#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
source "$ROOT/tests/e2e/scripts/process_tree_cleanup.sh"
STAMP="${ZF_E2E_RUN_TAG:-$(date -u +%Y%m%dT%H%M%SZ)}"
RUN_ROOT="${ZF_E2E_CANARY_ROOT:-/tmp/zf-oa-multiflow-blocking-canary-$STAMP}"
PRODUCT_RUNNER="$ROOT/tests/e2e/scripts/run_prod_new_three_workflow_e2e.sh"
COMPARATOR="$ROOT/tests/e2e/scripts/oa_full_workflow_ab_report.py"
GENERAL_RUNNER="$ROOT/tests/e2e/generic_workflow_real_provider_drill.py"
REPORTER="$ROOT/tests/e2e/scripts/oa_multiflow_blocking_canary_report.py"
BACKEND="${ZF_AGENT_BACKEND:-codex}"
MODEL="${ZF_E2E_MODEL:-gpt-5.5}"
REASONING_EFFORT="${ZF_E2E_REASONING_EFFORT:-low}"
ARM_BUDGET_USD="${ZF_E2E_ARM_BUDGET_USD:-40}"
EXECUTION_MODE="${ZF_E2E_EXECUTION_MODE:-serial}"
STARTED_EPOCH="$(date +%s)"
ZF_BIN="${ZF_BIN:-uv --project "$ROOT" run zf}"
children=()

stop_product_runtimes() {
  local config
  while IFS= read -r config; do
    (cd "$(dirname "$config")" && \
      $ZF_BIN stop --include-run-manager --clean-workdirs) || true
  done < <(find "$RUN_ROOT" -type f -name zf.yaml -print 2>/dev/null)
}

cleanup_interrupted() {
  local exit_code="$1"
  local pid
  trap - INT TERM
  stop_product_runtimes
  for pid in "${children[@]}"; do
    zf_e2e_terminate_process_tree "$pid"
  done
  children=()
  exit "$exit_code"
}

trap 'cleanup_interrupted 130' INT
trap 'cleanup_interrupted 143' TERM

if [[ "$BACKEND" != "codex" ]]; then
  echo "multiflow blocking canary currently requires the Codex pilot templates" >&2
  exit 2
fi
if [[ "$EXECUTION_MODE" != "serial" && "$EXECUTION_MODE" != "parallel" ]]; then
  echo "ZF_E2E_EXECUTION_MODE must be serial or parallel" >&2
  exit 2
fi
if [[ -n "$(git -C "$ROOT" status --porcelain=v1)" ]]; then
  echo "multiflow blocking canary requires a clean exact-source checkout" >&2
  exit 2
fi
mkdir -p "$RUN_ROOT"

run_pair() (
  local flow="$1"
  local pair_root="$RUN_ROOT/$flow"
  local rc=0
  local arm_pid=""
  cleanup_pair() {
    local exit_code="$1"
    trap - INT TERM
    if [[ -n "$arm_pid" ]]; then
      zf_e2e_terminate_process_tree "$arm_pid"
    fi
    exit "$exit_code"
  }
  trap 'cleanup_pair 130' INT
  trap 'cleanup_pair 143' TERM
  mkdir -p "$pair_root"
  for policy in shadow blocking; do
    ZF_E2E_ROOT="$pair_root/$policy" \
      ZF_E2E_RUN_TAG="$STAMP" \
      ZF_E2E_FLOW_SET="$flow" \
      ZF_E2E_OA_PLAN_POLICY="$policy" \
      ZF_E2E_MODEL="$MODEL" \
      ZF_E2E_REASONING_EFFORT="$REASONING_EFFORT" \
      ZF_GLOBAL_BUDGET_USD="$ARM_BUDGET_USD" \
      bash "$PRODUCT_RUNNER" \
      >"$pair_root/$policy.log" 2>&1 &
    arm_pid=$!
    if wait "$arm_pid"; then
      arm_rc=0
    else
      arm_rc=$?
    fi
    arm_pid=""
    if (( arm_rc != 0 )); then
      rc=1
      if [[ "$policy" == "shadow" ]]; then
        echo "$flow shadow arm failed; skipping invalid blocking comparison" \
          >"$pair_root/blocking.skipped"
        break
      fi
    fi
  done
  if ! uv --project "$ROOT" run python "$COMPARATOR" \
    --flow-kind "$flow" \
    --shadow-report "$pair_root/shadow/report.json" \
    --blocking-report "$pair_root/blocking/report.json" \
    --output "$pair_root/ab-report.json" \
    >"$pair_root/comparator.log" 2>&1; then
    rc=1
  fi
  return "$rc"
)

run_general() (
  local provider_pid=""
  cleanup_general() {
    local exit_code="$1"
    trap - INT TERM
    if [[ -n "$provider_pid" ]]; then
      zf_e2e_terminate_process_tree "$provider_pid"
    fi
    exit "$exit_code"
  }
  trap 'cleanup_general 130' INT
  trap 'cleanup_general 143' TERM
  uv --project "$ROOT" run python "$GENERAL_RUNNER" \
    --backend codex \
    --confirm-real \
    --timeout-seconds "${ZF_E2E_GENERAL_TIMEOUT_SECONDS:-600}" \
    --model "$MODEL" \
    --reasoning-effort "$REASONING_EFFORT" \
    >"$RUN_ROOT/general-report.json" 2>"$RUN_ROOT/general.log" &
  provider_pid=$!
  wait "$provider_pid"
)

wait_child() {
  local pid="$1"
  local variable="$2"
  local rc
  if wait "$pid"; then
    rc=0
  else
    rc=$?
  fi
  local retained=()
  local child
  for child in "${children[@]}"; do
    if [[ "$child" != "$pid" ]]; then
      retained+=("$child")
    fi
  done
  children=("${retained[@]}")
  printf -v "$variable" '%s' "$rc"
}

run_serial() {
  local variable="$1"
  shift
  "$@" &
  local pid=$!
  children+=("$pid")
  wait_child "$pid" "$variable"
}

if [[ "$EXECUTION_MODE" == "parallel" ]]; then
  run_pair prd &
  prd_pid=$!
  children+=("$prd_pid")
  run_pair issue &
  issue_pid=$!
  children+=("$issue_pid")
  run_pair refactor &
  refactor_pid=$!
  children+=("$refactor_pid")
  run_general &
  general_pid=$!
  children+=("$general_pid")

  wait_child "$prd_pid" prd_rc
  wait_child "$issue_pid" issue_rc
  wait_child "$refactor_pid" refactor_rc
  wait_child "$general_pid" general_rc
  children=()
else
  run_serial prd_rc run_pair prd
  run_serial issue_rc run_pair issue
  run_serial refactor_rc run_pair refactor
  run_serial general_rc run_general
fi

WALL_SECONDS="$(( $(date +%s) - STARTED_EPOCH ))"
set +e
uv --project "$ROOT" run python "$REPORTER" \
  --prd-report "$RUN_ROOT/prd/ab-report.json" \
  --issue-report "$RUN_ROOT/issue/ab-report.json" \
  --refactor-report "$RUN_ROOT/refactor/ab-report.json" \
  --general-report "$RUN_ROOT/general-report.json" \
  --wall-seconds "$WALL_SECONDS" \
  --execution-mode "$EXECUTION_MODE" \
  --output "$RUN_ROOT/canary-report.json" \
  >"$RUN_ROOT/reporter.log" 2>&1
report_rc=$?
set -e

printf 'prd_rc=%s issue_rc=%s refactor_rc=%s general_rc=%s report_rc=%s\n' \
  "$prd_rc" "$issue_rc" "$refactor_rc" "$general_rc" "$report_rc"
printf 'report: %s\n' "$RUN_ROOT/canary-report.json"
if (( prd_rc != 0 || issue_rc != 0 || refactor_rc != 0 || general_rc != 0 || report_rc != 0 )); then
  exit 1
fi
