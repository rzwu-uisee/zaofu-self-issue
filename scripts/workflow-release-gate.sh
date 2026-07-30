#!/usr/bin/env bash
# Deterministic workflow release gate. Real-provider validation is a separate tier.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
CACHE_ROOT="${XDG_CACHE_HOME:-$HOME/.cache}"
mkdir -p "$CACHE_ROOT"
BASE_TEMP="$(mktemp -d "$CACHE_ROOT/zf-workflow-release.XXXXXX")"
trap 'rm -rf "$BASE_TEMP"' EXIT

cd "$ROOT"
uv run pytest -q --no-cov --basetemp="$BASE_TEMP/pytest" \
  tests/test_run_admission.py \
  tests/test_scheduler_task_attempt.py \
  tests/test_task_attempt_readiness.py \
  tests/test_task_attempt_recovery.py \
  tests/test_result_submit.py \
  tests/test_call_result_admission.py \
  tests/test_artifact_query_service.py \
  tests/test_goal_dossier.py \
  tests/e2e/test_agent_owned_verification_mock_e2e.py \
  tests/e2e/test_generic_workflow_complex_mock_e2e.py \
  tests/e2e/test_recovery_control_loop_serial.py

bash scripts/dev-premerge-gate.sh --basetemp="$BASE_TEMP/sentinel"
