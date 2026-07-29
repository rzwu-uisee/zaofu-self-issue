#!/usr/bin/env bash

set -euo pipefail

ROOT="$(git rev-parse --show-toplevel)"
FIXTURE="$ROOT/tests/e2e/fixtures/kanban-real-coding"
RUN_ROOT=""
WEB_PORT=""
EVIDENCE_DIR="${ZF_PLAYWRIGHT_EVIDENCE_DIR:-}"
KEEP=0
DOCKER_IMAGE="${ZF_PLAYWRIGHT_IMAGE:-mcp/playwright:latest}"

usage() {
  cat <<'USAGE'
Usage: tests/e2e/scripts/run_kanban_agent_real_coding_e2e.sh [options]

Options:
  --run-root PATH  Isolated run root. Default: /tmp/zf-kanban-real-coding-<utc>
  --port PORT      Web port. Default: first free port at 8002+
  --evidence-dir PATH
                   Retain browser screenshots outside the run root
  --keep           Retain the run root after completion for diagnosis
  -h, --help       Show this help

This is a real-provider test. Playwright starts from the Web Kanban
dangerous_full default in an isolated temporary Git project. The test drives
two coding turns and proves there is no workspace-write fallback, while also
covering native provider-session resume plus hidden functional tests.
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
  echo "docker is required for the real Coding browser E2E" >&2
  exit 2
fi
if ! timeout 10s codex login status 2>&1 | rg -q "Logged in"; then
  echo "authenticated Codex CLI is required for the real Coding browser E2E" >&2
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
RUN_ROOT="${RUN_ROOT:-/tmp/zf-kanban-real-coding-${STAMP}}"
PROJECT_ROOT="$RUN_ROOT/project"
WORKSPACE_HOME="$RUN_ROOT/workspace-home"
EVIDENCE_DIR="${EVIDENCE_DIR:-$RUN_ROOT/evidence}"
STATE_DIR="$PROJECT_ROOT/.zf"
TOKEN="zf-real-coding-${STAMP}-$$"
WEB_PORT="${WEB_PORT:-$(pick_port 8002)}"
WEB_LOG="$RUN_ROOT/web.log"
WEB_PID=""
WEB_PGID=""
INITIALIZED=0

mkdir -p "$PROJECT_ROOT" "$WORKSPACE_HOME" "$EVIDENCE_DIR"
EVIDENCE_DIR="$(cd "$EVIDENCE_DIR" && pwd)"
cp "$FIXTURE/.gitignore" "$PROJECT_ROOT/.gitignore"
cp "$FIXTURE/zf.yaml" "$PROJECT_ROOT/zf.yaml"
cp "$FIXTURE/counter.py" "$PROJECT_ROOT/counter.py"
cp "$FIXTURE/test_counter.py" "$PROJECT_ROOT/test_counter.py"
git -C "$PROJECT_ROOT" init -q
git -C "$PROJECT_ROOT" config user.name "ZaoFu Real Coding E2E"
git -C "$PROJECT_ROOT" config user.email "real-coding-e2e@localhost"

cleanup() {
  local status="$?"
  set +e
  if [[ "$INITIALIZED" -eq 1 && -d "$STATE_DIR" ]]; then
    (
      cd "$PROJECT_ROOT" || exit 0
      ZF_STATE_DIR="$STATE_DIR" \
        ZF_WORKSPACE_HOME="$WORKSPACE_HOME" \
        PYTHONPATH="$ROOT/src" \
        "$ROOT/.venv/bin/zf" emit simulation.done \
          --actor real-coding-e2e \
          --payload '{"purpose":"kanban-agent-real-coding-playwright"}' \
          --state-dir "$STATE_DIR" >/dev/null 2>&1
    )
  fi
  if [[ -n "$WEB_PGID" ]]; then
    kill -TERM -- "-$WEB_PGID" >/dev/null 2>&1 || true
  elif [[ -n "$WEB_PID" ]]; then
    kill "$WEB_PID" >/dev/null 2>&1 || true
  fi
  if [[ "$KEEP" -eq 0 ]]; then
    find "$RUN_ROOT" -depth -delete
  else
    echo "[kept] $RUN_ROOT"
  fi
  exit "$status"
}
trap cleanup EXIT

echo "[setup] run_root=$RUN_ROOT port=$WEB_PORT"
(
  cd "$PROJECT_ROOT"
  ZF_STATE_DIR="$STATE_DIR" \
    ZF_WORKSPACE_HOME="$WORKSPACE_HOME" \
    PYTHONPATH="$ROOT/src" \
    "$ROOT/.venv/bin/zf" init \
      --force \
      --no-workspace-register \
      --no-git-hooks \
      --skip-instruction-docs
)
INITIALIZED=1

git -C "$PROJECT_ROOT" add .gitignore counter.py test_counter.py zf.yaml
git -C "$PROJECT_ROOT" commit -q -m "chore: initialize real coding fixture"

echo "[serve] http://0.0.0.0:$WEB_PORT"
(
  cd "$PROJECT_ROOT"
  exec setsid env \
    ZF_STATE_DIR="$STATE_DIR" \
    ZF_WORKSPACE_HOME="$WORKSPACE_HOME" \
    ZF_WEB_ACTION_TOKEN="$TOKEN" \
    ZF_KANBAN_AGENT_BACKEND=codex-headless \
    ZF_KANBAN_AGENT_HEADLESS_TIMEOUT_S=180 \
    ZF_CODEX_HEADLESS_TOOL_TIMEOUT_S=120 \
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
  -e ZF_REAL_CODING_PROJECT_ROOT=/zf-run/project \
  -e ZF_PLAYWRIGHT_EVIDENCE_DIR=/zf-evidence \
  "$DOCKER_IMAGE" \
  -lc 'set -euo pipefail; mkdir -p "$HOME"; timeout 180s ./node_modules/.bin/playwright install chromium; ./node_modules/.bin/playwright test tests/kanban-agent-real-coding.spec.ts --config playwright.config.ts --project=chromium --workers=1 --reporter=line --output=/zf-run/test-results'

(
  cd "$PROJECT_ROOT"
  PYTHONPATH="$PROJECT_ROOT" \
    "$ROOT/.venv/bin/python" -m unittest -q test_counter
)
PYTHONPATH="$PROJECT_ROOT" \
  "$ROOT/.venv/bin/python" \
    "$FIXTURE/_hidden/test_counter_start.py" -q

"$ROOT/.venv/bin/python" - \
  "$PROJECT_ROOT" "$STATE_DIR" <<'PY'
import json
import subprocess
import sys
from pathlib import Path

project_root = Path(sys.argv[1])
state_dir = Path(sys.argv[2])
events = [
    json.loads(line)
    for line in (state_dir / "events.jsonl").read_text(
        encoding="utf-8",
    ).splitlines()
    if line.strip()
]
coding_replies = [
    event for event in events
    if event.get("type") == "kanban.agent.reply"
    and event.get("payload", {}).get("backend") == "codex-headless"
    and "_DONE" in str(event.get("payload", {}).get("answer") or "")
]
completed = [
    event for event in events
    if event.get("type") == "kanban.agent.turn.completed"
    and event.get("payload", {}).get("backend") == "codex-headless"
]
snapshots = [
    event for event in events
    if event.get("type") == "provider.permission.snapshot.recorded"
    and event.get("causation_id") in {
        reply.get("id") for reply in coding_replies
    }
]
assert len(coding_replies) == 2, len(coding_replies)
assert len(completed) == 2, len(completed)
assert len(snapshots) == 2, len(snapshots)
profiles = {
    event.get("payload", {}).get("permission_profile")
    for event in snapshots
}
assert profiles == {"dangerous_full"}, profiles
for event in snapshots:
    snapshot = event.get("payload", {}).get("snapshot", {})
    assert snapshot.get("sandbox_policy") == "danger-full-access", snapshot
    assert snapshot.get("approval_policy") == "never", snapshot
sandbox_fallbacks = [
    event for event in events
    if event.get("type") == "kanban.agent.reply"
    and event.get("payload", {}).get("status") == "sandbox_unsupported"
]
permission_retries = [
    event for event in events
    if event.get("type") == "user.message"
    and event.get("payload", {}).get("permission_escalation_retry_for")
]
assert not sandbox_fallbacks, len(sandbox_fallbacks)
assert not permission_retries, len(permission_retries)
sessions = {
    event["payload"].get("provider_session_id")
    for event in coding_replies
}
assert len(sessions) == 1 and "" not in sessions, sessions
assert [event["payload"].get("resumed") for event in coding_replies] == [
    False,
    True,
]
changed = subprocess.run(
    ["git", "-C", str(project_root), "diff", "--name-only"],
    check=True,
    capture_output=True,
    text=True,
).stdout.splitlines()
assert changed == ["counter.py"], changed
assert subprocess.run(
    ["git", "-C", str(project_root), "rev-list", "--count", "HEAD"],
    check=True,
    capture_output=True,
    text=True,
).stdout.strip() == "1"
print(json.dumps({
    "coding_turns": len(completed),
    "codex_replies": len(coding_replies),
    "permission_profiles": sorted(profiles),
    "sandbox_fallbacks": len(sandbox_fallbacks),
    "permission_snapshots": len(snapshots),
    "provider_sessions": len(sessions),
    "resumed_turns": sum(
        bool(event["payload"].get("resumed"))
        for event in coding_replies
    ),
    "source_files_changed": changed,
    "functional_tests": 4,
}, sort_keys=True))
PY

cmp "$FIXTURE/test_counter.py" "$PROJECT_ROOT/test_counter.py"
echo "[pass] Kanban Agent real Codex coding browser E2E"
