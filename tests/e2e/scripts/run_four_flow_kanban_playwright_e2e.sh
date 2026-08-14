#!/usr/bin/env bash

set -euo pipefail

ROOT="$(git rev-parse --show-toplevel)"
COMMON_GIT_DIR="$(git rev-parse --path-format=absolute --git-common-dir)"
COMMON_ROOT="$(dirname "$COMMON_GIT_DIR")"
PYTHON_BIN="${ZF_E2E_PYTHON:-$COMMON_ROOT/.venv/bin/python}"
ZF_BIN="${ZF_E2E_ZF:-$COMMON_ROOT/.venv/bin/zf}"
NODE_MODULES="${ZF_E2E_NODE_MODULES:-$COMMON_ROOT/web/node_modules}"
DOCKER_IMAGE="${ZF_PLAYWRIGHT_IMAGE:-mcp/playwright:latest}"
RUN_ROOT=""
WEB_PORT=""
EVIDENCE_DIR="${ZF_PLAYWRIGHT_EVIDENCE_DIR:-}"
KEEP=0

usage() {
  printf '%s\n' \
    "Usage: tests/e2e/scripts/run_four_flow_kanban_playwright_e2e.sh [options]" \
    "" \
    "Options:" \
    "  --run-root PATH     Isolated run root (default: /tmp/zf-four-flow-<utc>)" \
    "  --port PORT         Web port (default: first free port at 8002+)" \
    "  --evidence-dir PATH Browser screenshots (default: inside run root)" \
    "  --keep              Retain the isolated run root after completion" \
    "  -h, --help          Show this help"
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --run-root)
      RUN_ROOT="$2"
      shift 2
      ;;
    --port)
      WEB_PORT="$2"
      shift 2
      ;;
    --evidence-dir)
      EVIDENCE_DIR="$2"
      shift 2
      ;;
    --keep)
      KEEP=1
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      printf 'unknown option: %s\n' "$1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

if [[ ! -x "$PYTHON_BIN" || ! -x "$ZF_BIN" ]]; then
  printf 'missing test Python/ZF binaries: %s %s\n' \
    "$PYTHON_BIN" "$ZF_BIN" >&2
  exit 2
fi
if [[ ! -x "$NODE_MODULES/.bin/playwright" ]]; then
  printf 'missing Playwright dependencies: %s\n' "$NODE_MODULES" >&2
  exit 2
fi
if ! command -v docker >/dev/null 2>&1; then
  printf 'docker is required for browser E2E\n' >&2
  exit 2
fi
if ! command -v tmux >/dev/null 2>&1; then
  printf 'tmux is required for workflow worker sessions\n' >&2
  exit 2
fi

pick_port() {
  "$PYTHON_BIN" -c '
import socket
import sys

port = int(sys.argv[1])
while port < 65535:
    with socket.socket() as sock:
        try:
            sock.bind(("127.0.0.1", port))
        except OSError:
            port += 1
            continue
        print(port)
        raise SystemExit(0)
raise SystemExit("no free port")
' "$1"
}

STAMP="$(date -u +%Y%m%d-%H%M%S)"
RUN_ROOT="${RUN_ROOT:-/tmp/zf-four-flow-${STAMP}}"
PROJECT_ROOT="$RUN_ROOT/project"
SOURCE_ROOT="$RUN_ROOT/refactor-source"
WORKSPACE_HOME="$RUN_ROOT/workspace-home"
STATE_DIR="$PROJECT_ROOT/.zf"
EVIDENCE_DIR="${EVIDENCE_DIR:-$RUN_ROOT/evidence}"
WEB_PORT="${WEB_PORT:-$(pick_port 8002)}"
TMUX_SESSION="zf-four-flow-${STAMP}-$$"
CHANNEL_ID="ch-four-flow-${STAMP}"
WORKFLOW_REQUEST_ID="REQ-FOUR-FLOW-${STAMP}"
TOKEN="zf-four-flow-${STAMP}-$$"
FAKE_PROVIDER="$ROOT/tests/e2e/scripts/four_flow_fake_headless.py"
SUPPORT="$ROOT/tests/e2e/scripts/four_flow_e2e_support.py"
RUNTIME_PID=""
RUNTIME_PGID=""
WEB_PID=""
WEB_PGID=""
RUNTIME_LOG=""
WEB_LOG=""
INITIALIZED=0

mkdir -p "$RUN_ROOT" "$WORKSPACE_HOME" "$EVIDENCE_DIR"
EVIDENCE_DIR="$(cd "$EVIDENCE_DIR" && pwd)"

stop_services() {
  set +e
  if [[ "$INITIALIZED" -eq 1 && -d "$STATE_DIR" ]]; then
    (
      cd "$PROJECT_ROOT" || exit 0
      env \
        ZF_STATE_DIR="$STATE_DIR" \
        ZF_TMUX_SESSION="$TMUX_SESSION" \
        ZF_WORKSPACE_HOME="$WORKSPACE_HOME" \
        PYTHONPATH="$ROOT/src" \
        "$ZF_BIN" stop --fast --include-run-manager
    ) >/dev/null 2>&1
  fi
  for pgid in "$WEB_PGID" "$RUNTIME_PGID"; do
    if [[ -n "$pgid" ]]; then
      kill -TERM -- "-$pgid" >/dev/null 2>&1
    fi
  done
  if [[ -n "$WEB_PID" ]]; then
    wait "$WEB_PID" >/dev/null 2>&1
  fi
  if [[ -n "$RUNTIME_PID" ]]; then
    wait "$RUNTIME_PID" >/dev/null 2>&1
  fi
  tmux kill-session -t "$TMUX_SESSION" >/dev/null 2>&1
  RUNTIME_PID=""
  RUNTIME_PGID=""
  WEB_PID=""
  WEB_PGID=""
  set -e
}

cleanup() {
  local status="$?"
  set +e
  if [[ "$INITIALIZED" -eq 1 && -d "$STATE_DIR" ]]; then
    (
      cd "$PROJECT_ROOT" || exit 0
      env \
        ZF_STATE_DIR="$STATE_DIR" \
        ZF_TMUX_SESSION="$TMUX_SESSION" \
        ZF_WORKSPACE_HOME="$WORKSPACE_HOME" \
        PYTHONPATH="$ROOT/src" \
        "$ZF_BIN" emit simulation.done \
          --actor four-flow-e2e \
          --payload '{"purpose":"four-flow-kanban-playwright"}' \
          --state-dir "$STATE_DIR"
    ) >/dev/null 2>&1
  fi
  stop_services
  if [[ "$KEEP" -eq 0 ]]; then
    find "$RUN_ROOT" -depth -delete
  else
    printf '[kept] %s\n' "$RUN_ROOT"
  fi
  exit "$status"
}
trap cleanup EXIT

start_services() {
  local phase="$1"
  RUNTIME_LOG="$RUN_ROOT/runtime-${phase}.log"
  WEB_LOG="$RUN_ROOT/web-${phase}.log"
  (
    cd "$PROJECT_ROOT"
    exec setsid env \
      ZF_STATE_DIR="$STATE_DIR" \
      ZF_TMUX_SESSION="$TMUX_SESSION" \
      ZF_WORKSPACE_HOME="$WORKSPACE_HOME" \
      ZF_KANBAN_AGENT_CLAUDE_HEADLESS_CMD="python3 $FAKE_PROVIDER" \
      ZF_FOUR_FLOW_CHANNEL_ID="$CHANNEL_ID" \
      ZF_FOUR_FLOW_SOURCE_ROOT="$SOURCE_ROOT" \
      PYTHONUNBUFFERED=1 \
      PYTHONPATH="$ROOT/src" \
      "$ZF_BIN" start
  ) >"$RUNTIME_LOG" 2>&1 &
  RUNTIME_PID="$!"
  RUNTIME_PGID="$(ps -o pgid= -p "$RUNTIME_PID" | tr -d ' ')"

  for _ in $(seq 1 120); do
    if ! kill -0 "$RUNTIME_PID" >/dev/null 2>&1; then
      printf 'runtime exited during %s startup\n' "$phase" >&2
      tail -160 "$RUNTIME_LOG" >&2
      return 1
    fi
    if tmux has-session -t "$TMUX_SESSION" >/dev/null 2>&1; then
      break
    fi
    sleep 1
  done
  if ! tmux has-session -t "$TMUX_SESSION" >/dev/null 2>&1; then
    printf 'runtime tmux session did not become ready during %s\n' "$phase" >&2
    tail -160 "$RUNTIME_LOG" >&2
    return 1
  fi

  (
    cd "$PROJECT_ROOT"
    exec setsid env \
      ZF_STATE_DIR="$STATE_DIR" \
      ZF_TMUX_SESSION="$TMUX_SESSION" \
      ZF_WORKSPACE_HOME="$WORKSPACE_HOME" \
      ZF_WEB_ACTION_TOKEN="$TOKEN" \
      ZF_KANBAN_AGENT_CLAUDE_HEADLESS_CMD="python3 $FAKE_PROVIDER" \
      ZF_KANBAN_AGENT_HEADLESS_TIMEOUT_S=30 \
      ZF_FOUR_FLOW_CHANNEL_ID="$CHANNEL_ID" \
      ZF_FOUR_FLOW_SOURCE_ROOT="$SOURCE_ROOT" \
      PYTHONUNBUFFERED=1 \
      PYTHONPATH="$ROOT/src" \
      "$ZF_BIN" web \
        --host 0.0.0.0 \
        --port "$WEB_PORT" \
        --state-dir "$STATE_DIR"
  ) >"$WEB_LOG" 2>&1 &
  WEB_PID="$!"
  WEB_PGID="$(ps -o pgid= -p "$WEB_PID" | tr -d ' ')"

  for _ in $(seq 1 90); do
    if curl -fsS \
      "http://127.0.0.1:$WEB_PORT/api/snapshot/light" \
      >/dev/null 2>&1; then
      break
    fi
    sleep 1
  done
  if ! curl -fsS \
    "http://127.0.0.1:$WEB_PORT/api/snapshot/light" \
    >/dev/null 2>&1; then
    printf 'web server did not become ready during %s\n' "$phase" >&2
    tail -160 "$WEB_LOG" >&2
    return 1
  fi

  curl -fsS -X POST \
    "http://127.0.0.1:$WEB_PORT/api/workspace/onboarding" \
    -H "Authorization: Bearer $TOKEN" \
    -H "Content-Type: application/json" \
    --data '{"action":"skip"}' >/dev/null
}

run_playwright() {
  local spec="$1"
  local output_name="$2"
  docker run --rm --network host \
    --user "$(id -u):$(id -g)" \
    --entrypoint bash \
    -v "$ROOT:/work" \
    -v "$NODE_MODULES:/work/web/node_modules" \
    -v "$RUN_ROOT:/zf-run" \
    -v "$EVIDENCE_DIR:/zf-evidence" \
    -w /work/web \
    -e HOME=/tmp/zf-playwright-home \
    -e PLAYWRIGHT_BROWSERS_PATH=0 \
    -e ZF_WEB_BASE_URL="http://127.0.0.1:$WEB_PORT" \
    -e ZF_WEB_ACTION_TOKEN_FOR_TEST="$TOKEN" \
    -e ZF_FOUR_FLOW_CHANNEL_ID="$CHANNEL_ID" \
    -e ZF_FOUR_FLOW_WORKFLOW_REQUEST_ID="$WORKFLOW_REQUEST_ID" \
    -e ZF_PLAYWRIGHT_EVIDENCE_DIR=/zf-evidence \
    "$DOCKER_IMAGE" \
    -lc "set -euo pipefail; mkdir -p \"\$HOME\"; chromium_path=\"\$(find /ms-playwright -type f -path '*/chrome-linux64/chrome' 2>/dev/null | sort | tail -1 || true)\"; if [[ -n \"\$chromium_path\" ]]; then export ZF_E2E_CHROMIUM_EXECUTABLE_PATH=\"\$chromium_path\"; else timeout 180s ./node_modules/.bin/playwright install chromium; fi; ./node_modules/.bin/playwright test \"$spec\" --config playwright.config.ts --project=chromium --workers=1 --reporter=line --output=/zf-run/$output_name"
}

printf '[build] production Web bundle from %s\n' "$ROOT"
docker run --rm \
  --user "$(id -u):$(id -g)" \
  --entrypoint bash \
  -v "$ROOT:/work" \
  -v "$NODE_MODULES:/work/web/node_modules" \
  -w /work/web \
  -e HOME=/tmp/zf-build-home \
  "$DOCKER_IMAGE" \
  -lc 'set -euo pipefail; mkdir -p "$HOME"; npm run build'

printf '[setup] run_root=%s port=%s tmux=%s\n' \
  "$RUN_ROOT" "$WEB_PORT" "$TMUX_SESSION"
env \
  ZF_WORKSPACE_HOME="$WORKSPACE_HOME" \
  PYTHONPATH="$ROOT/src" \
  "$ZF_BIN" project init \
    --kind multi \
    --name four-flow-e2e \
    --description "Isolated four-flow Kanban browser proof" \
    --root "$PROJECT_ROOT" \
    --backend mock \
    --lanes 1 \
    --state-dir .zf \
    --create \
    --git-init \
    --no-workspace-register \
    --skip-instruction-docs \
    --json >/dev/null
INITIALIZED=1

env PYTHONPATH="$ROOT/src" "$PYTHON_BIN" "$SUPPORT" prepare \
  --project-root "$PROJECT_ROOT" \
  --source-root "$SOURCE_ROOT"
git -C "$PROJECT_ROOT" config user.name "ZaoFu Four Flow E2E"
git -C "$PROJECT_ROOT" config user.email "four-flow-e2e@localhost"
git -C "$PROJECT_ROOT" add zf.yaml .gitignore
git -C "$PROJECT_ROOT" commit -q \
  -m "chore: configure isolated four-flow project"

printf '[start] phase 1: dynamic General Workflow synthesis and config apply\n'
start_services "install"
run_playwright \
  "tests/four-flow-workflow-install.spec.ts" \
  "test-results-install"
stop_services

git -C "$PROJECT_ROOT" add zf.yaml
git -C "$PROJECT_ROOT" commit -q \
  -m "chore: apply registered general workflow"
env PYTHONPATH="$ROOT/src" "$ZF_BIN" validate \
  --path "$PROJECT_ROOT/zf.yaml" \
  --cold-start >/dev/null

printf '[start] phase 2: Channel PRD and four workflow starts\n'
start_services "four-flows"
run_playwright \
  "tests/four-flow-kanban-prd.spec.ts" \
  "test-results-four-flows"

printf '[audit] canonical PRD, approval ordering, routes, and invokes\n'
env PYTHONPATH="$ROOT/src" "$PYTHON_BIN" "$SUPPORT" report \
  --project-root "$PROJECT_ROOT" \
  --state-dir "$STATE_DIR" \
  --channel-id "$CHANNEL_ID" \
  --workflow-request-id "$WORKFLOW_REQUEST_ID"
printf '[pass] four-flow Kanban browser E2E\n'
