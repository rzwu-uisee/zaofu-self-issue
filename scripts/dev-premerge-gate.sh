#!/usr/bin/env bash
# dev pre-merge 哨兵门(2026-07-04 立,基线红回潮防线)。
# 背景:07-03 基线红归零,07-04 一次 dev 合并即躺 13 红——多驱合并速度
# 下"谁发现谁修"追不上。合 dev 前必跑本门(<60s),红则不合。
# 哨兵集只挑"合并最易打红且秒级可跑"的合同类测试,不替代全量回归。
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
  tests/test_event_contracts.py \
  tests/test_registry_forcing_closure.py \
  tests/test_structure_discipline.py \
  tests/test_workflow_spine_projection.py \
  --no-cov -q "$@"
