#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
STAMP="${ZF_E2E_RUN_TAG:-$(date -u +%Y%m%dT%H%M%SZ)}"
RUN_ROOT="${ZF_E2E_AB_ROOT:-/tmp/zf-oa-full-workflow-ab-$STAMP}"
RUNNER="$ROOT/tests/e2e/scripts/run_prod_new_three_workflow_e2e.sh"
COMPARATOR="$ROOT/tests/e2e/scripts/oa_full_workflow_ab_report.py"

mkdir -p "$RUN_ROOT"

run_arm() {
  local arm="$1"
  local policy="$2"
  ZF_E2E_ROOT="$RUN_ROOT/$arm" \
  ZF_E2E_RUN_TAG="$STAMP" \
  ZF_E2E_FLOW_SET=prd \
  ZF_E2E_OA_PLAN_POLICY="$policy" \
    bash "$RUNNER"
}

set +e
run_arm shadow shadow >"$RUN_ROOT/shadow.log" 2>&1
shadow_rc=$?
run_arm blocking blocking >"$RUN_ROOT/blocking.log" 2>&1
blocking_rc=$?
set -e

set +e
uv --project "$ROOT" run python "$COMPARATOR" \
  --shadow-report "$RUN_ROOT/shadow/report.json" \
  --blocking-report "$RUN_ROOT/blocking/report.json" \
  --output "$RUN_ROOT/ab-report.json"
report_rc=$?
set -e

printf 'shadow_rc=%s blocking_rc=%s report_rc=%s\n' \
  "$shadow_rc" "$blocking_rc" "$report_rc"
printf 'report: %s\n' "$RUN_ROOT/ab-report.json"
if (( shadow_rc != 0 || blocking_rc != 0 || report_rc != 0 )); then
  exit 1
fi
