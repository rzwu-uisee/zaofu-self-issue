#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
source "$ROOT/tests/e2e/scripts/process_tree_cleanup.sh"
STAMP="${ZF_E2E_RUN_TAG:-$(date -u +%Y%m%dT%H%M%SZ)}"
RUN_ROOT="${ZF_E2E_FOUR_FLOW_ROOT:-/tmp/zf-oa-clean-four-flow-$STAMP}"
PRODUCT_RUNNER="$ROOT/tests/e2e/scripts/run_prod_new_three_workflow_e2e.sh"
GENERAL_RUNNER="$ROOT/tests/e2e/generic_workflow_real_provider_drill.py"
REPORTER="$ROOT/tests/e2e/scripts/oa_clean_four_flow_report.py"
BACKEND="${ZF_AGENT_BACKEND:-codex}"
ZF_BIN="${ZF_BIN:-uv --project "$ROOT" run zf}"
ACTIVE_CHILD_PID=""

stop_product_runtimes() {
  local config
  while IFS= read -r config; do
    (cd "$(dirname "$config")" && \
      $ZF_BIN stop --include-run-manager --clean-workdirs) || true
  done < <(find "$RUN_ROOT" -type f -name zf.yaml -print 2>/dev/null)
}

cleanup_interrupted() {
  local exit_code="$1"
  trap - INT TERM
  stop_product_runtimes
  if [[ -n "$ACTIVE_CHILD_PID" ]]; then
    zf_e2e_terminate_process_tree "$ACTIVE_CHILD_PID"
  fi
  exit "$exit_code"
}

trap 'cleanup_interrupted 130' INT
trap 'cleanup_interrupted 143' TERM

if [[ "${ZF_E2E_SIGNAL_CLEANUP_SELFTEST:-false}" == "true" ]]; then
  descendant_pid_file="${ZF_E2E_SIGNAL_SELFTEST_DESCENDANT_PID_FILE:-$RUN_ROOT/selftest-descendant.pid}"
  mkdir -p "$RUN_ROOT/product/product"
  : >"$RUN_ROOT/product/product/zf.yaml"
  bash -c 'sleep 300 & printf "%s\n" "$!" >"$1"; wait' \
    _ "$descendant_pid_file" &
  ACTIVE_CHILD_PID="$!"
  printf '%s\n' "$ACTIVE_CHILD_PID" >"$ZF_E2E_SIGNAL_SELFTEST_PID_FILE"
  while [[ ! -s "$descendant_pid_file" ]]; do
    sleep 0.05
  done
  kill -TERM "$$"
  exit 99
fi

if [[ "$BACKEND" != "codex" && "$BACKEND" != "claude-code" ]]; then
  echo "clean four-flow E2E requires codex or claude-code" >&2
  exit 2
fi
mkdir -p "$RUN_ROOT"

set +e
ZF_E2E_ROOT="$RUN_ROOT/product" \
ZF_E2E_RUN_TAG="$STAMP" \
ZF_E2E_FLOW_SET=prd,issue,refactor \
  bash "$PRODUCT_RUNNER" >"$RUN_ROOT/product.log" 2>&1 &
ACTIVE_CHILD_PID=$!
wait "$ACTIVE_CHILD_PID"
product_rc=$?
ACTIVE_CHILD_PID=""

uv --project "$ROOT" run python "$GENERAL_RUNNER" \
  --backend "$BACKEND" \
  --confirm-real \
  --timeout-seconds "${ZF_E2E_GENERAL_TIMEOUT_SECONDS:-600}" \
  --model "${ZF_E2E_MODEL:-gpt-5.5}" \
  --reasoning-effort "${ZF_E2E_REASONING_EFFORT:-low}" \
  >"$RUN_ROOT/general-report.json" 2>"$RUN_ROOT/general.log" &
ACTIVE_CHILD_PID=$!
wait "$ACTIVE_CHILD_PID"
general_rc=$?
ACTIVE_CHILD_PID=""

uv --project "$ROOT" run python "$REPORTER" \
  --product-report "$RUN_ROOT/product/report.json" \
  --general-report "$RUN_ROOT/general-report.json" \
  --output "$RUN_ROOT/four-flow-report.json"
report_rc=$?
set -e

printf 'product_rc=%s general_rc=%s report_rc=%s\n' \
  "$product_rc" "$general_rc" "$report_rc"
printf 'report: %s\n' "$RUN_ROOT/four-flow-report.json"
if (( product_rc != 0 || general_rc != 0 || report_rc != 0 )); then
  exit 1
fi
