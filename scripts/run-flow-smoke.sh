#!/usr/bin/env bash
# E4:三流最小 smoke(kernel/workflow/profile 改动后的固定回归入口)。
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
PY="${ZF_PYTHON:-}"
if [ -z "$PY" ] && [ -x "$ROOT/.venv/bin/python" ]; then
  PY="$ROOT/.venv/bin/python"
fi
COMMON_GIT="$(git rev-parse --git-common-dir)"
case "$COMMON_GIT" in
  /*) ;;
  *) COMMON_GIT="$ROOT/$COMMON_GIT" ;;
esac
COMMON_ROOT="$(cd "$(dirname "$COMMON_GIT")" && pwd)"
if [ -z "$PY" ] && [ -x "$COMMON_ROOT/.venv/bin/python" ]; then
  PY="$COMMON_ROOT/.venv/bin/python"
fi
PY="${PY:-$(command -v python3)}"
exec env PYTHONPATH=src "$PY" -m pytest \
  tests/test_flow_smoke_e2e.py \
  tests/test_controller_flow_smoke_matrix.py \
  -q --no-cov -p no:cacheprovider "$@"
