#!/usr/bin/env bash

set -euo pipefail

ROOT="$(git rev-parse --show-toplevel)"
RUN_ROOT=""
WEB_PORT=""
EVIDENCE_DIR="${ZF_PLAYWRIGHT_EVIDENCE_DIR:-}"
KEEP=0
DOCKER_IMAGE="${ZF_PLAYWRIGHT_IMAGE:-mcp/playwright:latest}"

usage() {
  cat <<'USAGE'
Usage: tests/e2e/scripts/run_doc156_kanban_collaboration_e2e.sh [options]

Options:
  --run-root PATH  Isolated run root. Default: /tmp/zf-doc156-kanban-<utc>
  --port PORT      Web port. Default: first free port at 8002+
  --evidence-dir PATH
                   Retain browser screenshots outside the run root
  --keep           Retain the run root after completion for diagnosis
  -h, --help       Show this help

The Kanban conversation and Research outputs are deterministic. The
delivery-smoke role uses the authenticated Codex CLI and is stopped after the
provider accepts its first task prompt.
USAGE
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --run-root) RUN_ROOT="$2"; shift 2 ;;
    --port) WEB_PORT="$2"; shift 2 ;;
    --evidence-dir) EVIDENCE_DIR="$2"; shift 2 ;;
    --keep) KEEP=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "unknown option: $1" >&2; usage >&2; exit 2 ;;
  esac
done

if [[ ! -x "$ROOT/.venv/bin/zf" ]]; then
  echo "missing $ROOT/.venv; run: uv sync --extra dev --extra web" >&2
  exit 2
fi
if [[ ! -x "$ROOT/web/node_modules/.bin/playwright" ]]; then
  echo "missing web/node_modules; run: npm ci --prefix web" >&2
  exit 2
fi
if ! command -v docker >/dev/null 2>&1; then
  echo "docker is required for Doc 156 browser E2E" >&2
  exit 2
fi
if ! command -v tmux >/dev/null 2>&1; then
  echo "tmux is required for the live Workflow dispatch" >&2
  exit 2
fi
if ! timeout 10s codex login status 2>&1 | rg -q "Logged in"; then
  echo "authenticated Codex CLI is required for delivery-smoke" >&2
  exit 2
fi

echo "[build] web production bundle"
npm --prefix "$ROOT/web" run build

pick_port() {
  "$ROOT/.venv/bin/python" - "$1" <<'PY'
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
PY
}

STAMP="$(date -u +%Y%m%d-%H%M%S)"
RUN_ROOT="${RUN_ROOT:-/tmp/zf-doc156-kanban-${STAMP}}"
PROJECT_ROOT="$RUN_ROOT/project"
WORKSPACE_HOME="$RUN_ROOT/workspace-home"
EVIDENCE_DIR="${EVIDENCE_DIR:-$RUN_ROOT/evidence}"
STATE_DIR="$PROJECT_ROOT/.zf"
TMUX_SESSION="zf-doc156-kanban-${STAMP}-$$"
CHANNEL_ID="ch-doc156-live-${STAMP}"
REQUEST_ID="REQ-DOC156-${STAMP}"
TOKEN="zf-doc156-${STAMP}-$$"
WEB_PORT="${WEB_PORT:-$(pick_port 8002)}"
RUNTIME_LOG="$RUN_ROOT/runtime.log"
WEB_LOG="$RUN_ROOT/web.log"
RESEARCH_LOG="$RUN_ROOT/research.log"
RUNTIME_PID=""
RUNTIME_PGID=""
WEB_PID=""
WEB_PGID=""
RESEARCH_PID=""
INITIALIZED=0

mkdir -p "$PROJECT_ROOT" "$WORKSPACE_HOME" "$EVIDENCE_DIR"
EVIDENCE_DIR="$(cd "$EVIDENCE_DIR" && pwd)"
cp "$ROOT/tests/e2e/fixtures/doc156-kanban-collaboration-live.yaml" "$PROJECT_ROOT/zf.yaml"
git -C "$PROJECT_ROOT" init -q
git -C "$PROJECT_ROOT" config user.name "ZaoFu Doc156 E2E"
git -C "$PROJECT_ROOT" config user.email "doc156-e2e@localhost"

cleanup() {
  local status="$?"
  set +e
  if [[ "$INITIALIZED" -eq 1 && -d "$STATE_DIR" ]]; then
    (
      cd "$PROJECT_ROOT" || exit 0
      ZF_STATE_DIR="$STATE_DIR" \
        ZF_TMUX_SESSION="$TMUX_SESSION" \
        ZF_WORKSPACE_HOME="$WORKSPACE_HOME" \
        PYTHONPATH="$ROOT/src" \
        "$ROOT/.venv/bin/zf" emit simulation.done \
          --actor doc156-e2e \
          --payload '{"purpose":"doc156-kanban-collaboration-playwright"}' \
          --state-dir "$STATE_DIR" >/dev/null 2>&1
      ZF_STATE_DIR="$STATE_DIR" \
        ZF_TMUX_SESSION="$TMUX_SESSION" \
        ZF_WORKSPACE_HOME="$WORKSPACE_HOME" \
        PYTHONPATH="$ROOT/src" \
        "$ROOT/.venv/bin/zf" stop --fast --include-run-manager >/dev/null 2>&1
    )
  fi
  if [[ -n "$RESEARCH_PID" ]]; then
    kill "$RESEARCH_PID" >/dev/null 2>&1 || true
  fi
  for pgid in "$WEB_PGID" "$RUNTIME_PGID"; do
    if [[ -n "$pgid" ]]; then
      kill -TERM -- "-$pgid" >/dev/null 2>&1 || true
    fi
  done
  tmux kill-session -t "$TMUX_SESSION" >/dev/null 2>&1 || true
  if [[ "$KEEP" -eq 0 ]]; then
    find "$RUN_ROOT" -depth -delete
  else
    echo "[kept] $RUN_ROOT"
  fi
  exit "$status"
}
trap cleanup EXIT

echo "[setup] run_root=$RUN_ROOT port=$WEB_PORT tmux=$TMUX_SESSION"
(
  cd "$PROJECT_ROOT"
  ZF_STATE_DIR="$STATE_DIR" \
    ZF_TMUX_SESSION="$TMUX_SESSION" \
    ZF_WORKSPACE_HOME="$WORKSPACE_HOME" \
    PYTHONPATH="$ROOT/src" \
    "$ROOT/.venv/bin/zf" init \
      --force \
      --no-workspace-register \
      --no-git-hooks \
      --skip-instruction-docs
)
INITIALIZED=1

git -C "$PROJECT_ROOT" add zf.yaml
git -C "$PROJECT_ROOT" commit -q -m "chore: initialize doc156 live project"

TASK_ID="$(
  cd "$PROJECT_ROOT"
  ZF_STATE_DIR="$STATE_DIR" \
    ZF_TMUX_SESSION="$TMUX_SESSION" \
    ZF_WORKSPACE_HOME="$WORKSPACE_HOME" \
    PYTHONPATH="$ROOT/src" \
    "$ROOT/.venv/bin/zf" kanban add \
      --id-only \
      --key "doc156-live-${STAMP}" \
      "Doc 156 live collaboration workflow"
)"
(
  cd "$PROJECT_ROOT"
  ZF_STATE_DIR="$STATE_DIR" \
    ZF_TMUX_SESSION="$TMUX_SESSION" \
  ZF_WORKSPACE_HOME="$WORKSPACE_HOME" \
    PYTHONPATH="$ROOT/src" \
    "$ROOT/.venv/bin/zf" kanban move "$TASK_ID" in_progress >/dev/null
  ZF_STATE_DIR="$STATE_DIR" \
    ZF_TMUX_SESSION="$TMUX_SESSION" \
    ZF_WORKSPACE_HOME="$WORKSPACE_HOME" \
    PYTHONPATH="$ROOT/src" \
    "$ROOT/.venv/bin/zf" kanban move "$TASK_ID" review >/dev/null
)
export ZF_DOC156_TASK_ID="$TASK_ID"
export ZF_DOC156_CHANNEL_ID="$CHANNEL_ID"
export ZF_DOC156_REQUEST_ID="$REQUEST_ID"

PYTHONPATH="$ROOT/src" "$ROOT/.venv/bin/python" \
  "$ROOT/tests/e2e/scripts/doc156_research_finisher.py" \
  prepare-request \
  --project-root "$PROJECT_ROOT" \
  --state-dir "$STATE_DIR" \
  --task-id "$TASK_ID" \
  --channel-id "$CHANNEL_ID" \
  --request-id "$REQUEST_ID" >/dev/null

echo "[start] Kernel runtime with real Codex delivery role"
(
  cd "$PROJECT_ROOT"
  exec setsid env \
    ZF_STATE_DIR="$STATE_DIR" \
    ZF_TMUX_SESSION="$TMUX_SESSION" \
    ZF_WORKSPACE_HOME="$WORKSPACE_HOME" \
    ZF_CODEX_WORKER_SANDBOX=read-only \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH="$ROOT/src" \
    "$ROOT/.venv/bin/zf" start
) >"$RUNTIME_LOG" 2>&1 &
RUNTIME_PID="$!"
RUNTIME_PGID="$(ps -o pgid= -p "$RUNTIME_PID" | tr -d ' ')"

for _ in $(seq 1 120); do
  if ! kill -0 "$RUNTIME_PID" >/dev/null 2>&1; then
    echo "Kernel runtime exited during startup" >&2
    tail -120 "$RUNTIME_LOG" >&2 || true
    exit 1
  fi
  if tmux has-session -t "$TMUX_SESSION" >/dev/null 2>&1 \
    && rg -q "delivery_worker: ready" "$RUNTIME_LOG"; then
    break
  fi
  sleep 1
done
if ! rg -q "delivery_worker: ready" "$RUNTIME_LOG"; then
  echo "Codex delivery worker did not become ready" >&2
  tail -120 "$RUNTIME_LOG" >&2 || true
  exit 1
fi

PYTHONPATH="$ROOT/src" "$ROOT/.venv/bin/python" \
  "$ROOT/tests/e2e/scripts/doc156_research_finisher.py" \
  finish-research \
  --project-root "$PROJECT_ROOT" \
  --state-dir "$STATE_DIR" \
  --task-id "$TASK_ID" \
  --channel-id "$CHANNEL_ID" \
  --request-id "$REQUEST_ID" \
  --timeout 180 >"$RESEARCH_LOG" 2>&1 &
RESEARCH_PID="$!"

echo "[serve] http://0.0.0.0:$WEB_PORT"
(
  cd "$PROJECT_ROOT"
  exec setsid env \
    ZF_STATE_DIR="$STATE_DIR" \
    ZF_TMUX_SESSION="$TMUX_SESSION" \
    ZF_WORKSPACE_HOME="$WORKSPACE_HOME" \
    ZF_WEB_ACTION_TOKEN="$TOKEN" \
    ZF_DOC156_TASK_ID="$TASK_ID" \
    ZF_DOC156_CHANNEL_ID="$CHANNEL_ID" \
    ZF_DOC156_REQUEST_ID="$REQUEST_ID" \
    ZF_KANBAN_AGENT_CLAUDE_HEADLESS_CMD="python3 $ROOT/tests/e2e/scripts/doc156_fake_kanban_agent.py" \
    ZF_KANBAN_AGENT_HEADLESS_TIMEOUT_S=30 \
    PYTHONPATH="$ROOT/src" \
    "$ROOT/.venv/bin/zf" web \
      --host 0.0.0.0 \
      --port "$WEB_PORT" \
      --state-dir "$STATE_DIR"
) >"$WEB_LOG" 2>&1 &
WEB_PID="$!"
WEB_PGID="$(ps -o pgid= -p "$WEB_PID" | tr -d ' ')"

for _ in $(seq 1 90); do
  if curl -fsS "http://127.0.0.1:$WEB_PORT/api/snapshot/light" >/dev/null 2>&1; then
    break
  fi
  sleep 1
done
if ! curl -fsS "http://127.0.0.1:$WEB_PORT/api/snapshot/light" >/dev/null 2>&1; then
  echo "Web server did not become ready" >&2
  tail -120 "$WEB_LOG" >&2 || true
  exit 1
fi

curl -fsS -X POST "http://127.0.0.1:$WEB_PORT/api/workspace/onboarding" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  --data '{"action":"skip"}' >/dev/null

echo "[playwright] Docker image $DOCKER_IMAGE"
docker run --rm --network host \
  --user "$(id -u):$(id -g)" \
  --entrypoint bash \
  -v "$ROOT:/work" \
  -v "$RUN_ROOT:/zf-run" \
  -v "$EVIDENCE_DIR:/zf-evidence" \
  -w /work/web \
  -e HOME=/tmp/zf-playwright-home \
  -e PLAYWRIGHT_BROWSERS_PATH=0 \
  -e ZF_WEB_BASE_URL="http://127.0.0.1:$WEB_PORT" \
  -e ZF_WEB_ACTION_TOKEN_FOR_TEST="$TOKEN" \
  -e ZF_DOC156_TASK_ID="$TASK_ID" \
  -e ZF_DOC156_CHANNEL_ID="$CHANNEL_ID" \
  -e ZF_DOC156_REQUEST_ID="$REQUEST_ID" \
  -e ZF_PLAYWRIGHT_EVIDENCE_DIR=/zf-evidence \
  "$DOCKER_IMAGE" \
  -lc 'set -euo pipefail; mkdir -p "$HOME"; timeout 180s ./node_modules/.bin/playwright install chromium; ./node_modules/.bin/playwright test tests/kanban-agent-collaboration.spec.ts --config playwright.config.ts --project=chromium --workers=1 --reporter=line --output=/zf-run/test-results'

wait "$RESEARCH_PID"
RESEARCH_PID=""
PYTHONPATH="$ROOT/src" "$ROOT/.venv/bin/python" \
  "$ROOT/tests/e2e/scripts/doc156_research_finisher.py" \
  report \
  --project-root "$PROJECT_ROOT" \
  --state-dir "$STATE_DIR" \
  --task-id "$TASK_ID" \
  --channel-id "$CHANNEL_ID" \
  --request-id "$REQUEST_ID"
echo "[pass] Doc 156 Kanban collaboration live browser E2E"
