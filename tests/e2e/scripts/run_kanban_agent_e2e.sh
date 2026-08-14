#!/usr/bin/env bash

set -euo pipefail

ROOT="$(git rev-parse --show-toplevel)"
RUN_ROOT="${ZF_KANBAN_AGENT_E2E_RUN_ROOT:-}"
WEB_PORT="${ZF_KANBAN_AGENT_E2E_PORT:-}"
EVIDENCE_DIR="${ZF_PLAYWRIGHT_EVIDENCE_DIR:-}"
DOCKER_IMAGE="${ZF_PLAYWRIGHT_IMAGE:-mcp/playwright:latest}"
PLAYWRIGHT_INSTALL_TIMEOUT_S="${ZF_PLAYWRIGHT_INSTALL_TIMEOUT_S:-600}"
KEEP=0

usage() {
  cat <<'USAGE'
Usage: tests/e2e/scripts/run_kanban_agent_e2e.sh [--run-root PATH] [--port PORT] [--evidence-dir PATH] [--keep]

Runs the deterministic Kanban Agent fake-provider suite in Docker Playwright.
Successful runs clean their temporary state by default; failed runs are retained.
Screenshots are retained when --evidence-dir points outside the run root.
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

if [[ ! -x "$ROOT/.venv/bin/zf" ]]; then
  echo "missing $ROOT/.venv; run: uv sync --extra dev --extra web" >&2
  exit 2
fi
if [[ ! -x "$ROOT/web/node_modules/.bin/playwright" ]]; then
  echo "missing web/node_modules; run: npm ci --prefix web" >&2
  exit 2
fi
if ! command -v docker >/dev/null 2>&1; then
  echo "docker is required for Kanban Agent browser E2E" >&2
  exit 2
fi

echo "[build] web production bundle"
npm --prefix "$ROOT/web" run build

STAMP="$(date -u +%Y%m%d-%H%M%S)"
RUN_ROOT="${RUN_ROOT:-/tmp/zf-kanban-agent-e2e-${STAMP}}"
PROJECT_ROOT="$RUN_ROOT/project"
WORKSPACE_HOME="$RUN_ROOT/workspace-home"
EVIDENCE_DIR="${EVIDENCE_DIR:-$RUN_ROOT/evidence}"
STATE_DIR="$PROJECT_ROOT/.zf"
FAKE_CLAUDE="$RUN_ROOT/fake_claude.py"
WEB_LOG="$RUN_ROOT/web.log"
TOKEN="zf-kanban-e2e-${STAMP}-$$"
TOKEN_SHA256="$(printf '%s' "$TOKEN" | sha256sum | cut -d' ' -f1)"
WEB_PID=""
WEB_PGID=""
SIM_INITIALIZED=0

mkdir -p "$PROJECT_ROOT" "$WORKSPACE_HOME" "$EVIDENCE_DIR"
EVIDENCE_DIR="$(cd "$EVIDENCE_DIR" && pwd)"

cat >"$FAKE_CLAUDE" <<'PY'
#!/usr/bin/env python3
from __future__ import annotations

import json
import re
import sys
import time


def emit(value: dict) -> None:
    print(json.dumps(value, ensure_ascii=False), flush=True)


args = sys.argv[1:]
provider_session_id = ""
for flag in ("--resume", "--session-id"):
    if flag in args:
        index = args.index(flag)
        if index + 1 < len(args):
            provider_session_id = args[index + 1]
            break
provider_session_id = provider_session_id or "fake-kanban-session"

raw = sys.stdin.readline()
try:
    payload = json.loads(raw)
except json.JSONDecodeError:
    payload = {"raw": raw}
text = json.dumps(payload, ensure_ascii=False)
quickstart_channel_request = (
    "Create a focused collaboration Channel for an API authentication review "
    "and start it."
)
if quickstart_channel_request in text:
    marker = "KBA_CHANNEL_QUICKSTART"
else:
    matches = re.findall(r"KBA_[A-Z_]+_[a-z0-9]+", text, re.IGNORECASE)
    marker = matches[-1] if matches else "KBA_FAKE_DEFAULT"

emit({"type": "system", "session_id": provider_session_id})
emit({
    "type": "assistant",
    "session_id": provider_session_id,
    "message": {"content": [{"type": "text", "text": f"{marker} streamed response"}]},
})

if marker.startswith("KBA_HOLD_"):
    time.sleep(120)
    raise SystemExit(9)

time.sleep(1.5)
if marker.startswith("KBA_CHANNEL_"):
    result = {
        "plan_request": {
            "subject_type": "channel_setup",
            "header": "Channel setup",
            "id": "channel-setup",
            "question": "Which collaboration setup should review the API authentication change?",
            "discussion_seed": "Review the API authentication change.",
            "submit_action": "channel-create-and-start",
            "submit_label": "Create & start",
            "options": [
                {
                    "id": "quick",
                    "label": "Quick change (Recommended)",
                    "description": "Three focused roles and four rounds.",
                    "recommended": True,
                    "submit_payload": {
                        "template_id": "quick-change",
                        "mode": "conversation",
                        "name": "API authentication review",
                        "overrides": {
                            "backend": "fake",
                            "budget": {"max_rounds": 4},
                        },
                    },
                },
                {
                    "id": "architecture",
                    "label": "Architecture review",
                    "description": "Broader architecture and security review.",
                    "submit_payload": {
                        "template_id": "architecture-review",
                        "mode": "multi_lens",
                        "name": "API authentication architecture review",
                        "overrides": {
                            "backend": "fake",
                            "budget": {"max_rounds": 6},
                        },
                    },
                },
            ],
            "allow_other": False,
            "reason": "The role set and turn budget change collaboration cost.",
        }
    }
    reply = json.dumps(result, ensure_ascii=False)
elif marker.startswith("KBA_TASK_WORKFLOW_"):
    result = {
        "action_proposal": {
            "action": "create-task",
            "intent": {
                "decision": "propose_action",
                "source_quote": "create a Task",
            },
            "payload": {
                "title": f"Task workflow {marker}",
                "priority": 2,
                "contract": {
                    "behavior": f"Run the selected workflow for {marker}.",
                    "verification": "Verify the bound workflow invoke event.",
                    "acceptance": "The workflow starts only after Plan and Approve.",
                },
                "workflow_plan": {
                    "header": "Workflow route",
                    "question_id": "workflow-route",
                    "question": f"How should Task workflow {marker} run?",
                    "options": [
                        {
                            "id": "research",
                            "label": "Research first (Recommended)",
                            "description": "Use the fixed reader fanout and synthesis.",
                            "recommended": True,
                            "route_id": "research:fixed",
                            "parameters": {
                                "expected_output": "Evidence-backed recommendation.",
                            },
                        },
                        {
                            "id": "general",
                            "label": "Delivery smoke review",
                            "description": "Use the registered general reader route.",
                            "route_id": "general:delivery-smoke",
                        },
                        {
                            "id": "defer",
                            "label": "No workflow yet",
                            "description": "Keep the Task tracked without execution.",
                            "mode": "defer",
                        },
                    ],
                    "allow_other": True,
                    "reason": "The selected route changes topology and output.",
                },
            },
            "reason": "Create the Task before choosing its execution route.",
        }
    }
    reply = json.dumps(result, ensure_ascii=False)
elif marker.startswith("KBA_PLAN_DISCUSS_"):
    reply = f"{marker} compared the routes without proposing or answering"
elif marker.startswith("KBA_PLAN_") and "Answer:" not in text:
    result = {
        "plan_request": {
            "header": "Delivery route",
            "id": "route",
            "question": f"Which route should create the task for {marker}?",
            "options": [
                {
                    "id": "direct",
                    "label": "Direct (Recommended)",
                    "description": "Create one tracked task now.",
                },
                {
                    "id": "research",
                    "label": "Research",
                    "description": "Collect evidence before creating work.",
                },
            ],
            "allow_other": True,
            "reason": "The route changes the next controlled action.",
        }
    }
    reply = json.dumps(result, ensure_ascii=False)
elif marker.startswith("KBA_PLAN_"):
    result = {
        "action_proposal": {
            "action": "create-task",
            "intent": {
                "decision": "propose_action",
                "source_quote": "create a task",
            },
            "payload": {
                "title": f"Kanban Plan delivery {marker}",
                "priority": 2,
                "contract": {
                    "behavior": f"Implement the route selected for {marker}.",
                    "verification": "uv run pytest -q --no-cov tests/test_web_headless_agent.py",
                    "acceptance": "The task is created only after Approve.",
                },
            },
            "reason": "The owner answered the durable Plan request.",
        }
    }
    reply = json.dumps(result, ensure_ascii=False)
elif marker.startswith("KBA_MULTI_PLAN_") and "Answer:" not in text:
    result = {
        "plan_request": {
            "subject_type": "clarification",
            "header": "Delivery inputs",
            "questions": [
                {
                    "id": "route",
                    "header": "Route",
                    "question": f"Which route should handle {marker}?",
                    "options": [
                        {
                            "id": "direct",
                            "label": "Direct",
                            "description": "Proceed from the current contract.",
                            "recommended": True,
                        },
                        {
                            "id": "research",
                            "label": "Research",
                            "description": "Collect broader evidence first.",
                        },
                    ],
                    "allow_other": True,
                },
                {
                    "id": "evidence",
                    "header": "Evidence",
                    "question": "Which evidence depth is required?",
                    "options": [
                        {
                            "id": "focused",
                            "label": "Focused",
                            "description": "Cover the changed contract and callers.",
                            "recommended": True,
                        },
                        {
                            "id": "broad",
                            "label": "Broad",
                            "description": "Run a wider project audit.",
                        },
                    ],
                    "allow_other": True,
                },
            ],
            "reason": "Both owner choices materially change the delivery plan.",
        }
    }
    reply = json.dumps(result, ensure_ascii=False)
elif marker.startswith("KBA_MULTI_PLAN_"):
    reply = f"{marker} accepted both Plan answers without proposing an action"
elif marker.startswith("KBA_INVALID_"):
    result = {
        "action_proposal": {
            "action": "create-task",
            "intent": {
                "decision": "propose_action",
                "source_quote": "create this task now",
            },
            "payload": {
                "title": f"Invalid Kanban Agent proposal {marker}",
                "contract": {
                    "behavior": "This proposal must remain non-executable.",
                    "verification": "The UI shows the binding error and disables approval.",
                },
            },
            "reason": "Deliberately mismatched semantic evidence for browser E2E.",
        }
    }
    reply = json.dumps(result, ensure_ascii=False)
elif marker.startswith("KBA_CREATE_"):
    result = {
        "action_proposal": {
            "action": "create-task",
            "intent": {
                "decision": "propose_action",
                "source_quote": "create a task proposal",
            },
            "payload": {
                "title": f"Kanban Agent proposal {marker}",
                "priority": 2,
                "contract": {
                    "behavior": f"Track the deterministic Kanban Agent E2E marker {marker}.",
                    "verification": "uv run pytest -q --no-cov tests/test_web_headless_agent.py",
                    "acceptance": "The task is created only after explicit operator acceptance.",
                },
            },
            "reason": "Explicit create-task request from the E2E operator.",
        }
    }
    reply = json.dumps(result, ensure_ascii=False)
else:
    reply = f"{marker} completed without a state-changing proposal"

emit({
    "type": "result",
    "session_id": provider_session_id,
    "result": reply,
    "usage": {"input_tokens": 12, "output_tokens": 8},
})
PY
chmod +x "$FAKE_CLAUDE"

cleanup() {
  local status="$?"
  set +e
  if [[ "$SIM_INITIALIZED" -eq 1 && -d "$STATE_DIR" ]]; then
    (
      cd "$PROJECT_ROOT" || exit 0
      ZF_WORKSPACE_HOME="$WORKSPACE_HOME" \
        PYTHONPATH="$ROOT/src" \
        "$ROOT/.venv/bin/zf" emit simulation.done \
          --actor e2e \
          --payload '{"purpose":"kanban-agent-playwright","runner":"run_kanban_agent_e2e.sh"}' \
          --state-dir "$STATE_DIR" >/dev/null 2>&1
    )
  fi
  if [[ -n "$WEB_PGID" ]]; then
    kill -TERM -- "-$WEB_PGID" >/dev/null 2>&1 || true
    for _ in $(seq 1 20); do
      kill -0 -- "-$WEB_PGID" >/dev/null 2>&1 || break
      sleep 0.25
    done
  elif [[ -n "$WEB_PID" ]]; then
    kill -TERM "$WEB_PID" >/dev/null 2>&1 || true
  fi
  if [[ "$status" -eq 0 && "$KEEP" -eq 0 ]]; then
    find "$RUN_ROOT" -depth -delete
  else
    echo "[kept] $RUN_ROOT"
  fi
  exit "$status"
}
trap cleanup EXIT

echo "[setup] run_root=$RUN_ROOT port=${WEB_PORT:-auto} token_sha256=$TOKEN_SHA256"
(
  cd "$PROJECT_ROOT"
  ZF_WORKSPACE_HOME="$WORKSPACE_HOME" \
    PYTHONPATH="$ROOT/src" \
    "$ROOT/.venv/bin/zf" init --preset minimal --force
)
SIM_INITIALIZED=1
cp "$ROOT/tests/e2e/fixtures/doc156-kanban-collaboration-live.yaml" \
  "$PROJECT_ROOT/zf.yaml"
WEB_PORT="${WEB_PORT:-$(pick_port 8002)}"

echo "[serve] http://0.0.0.0:$WEB_PORT"
(
  cd "$PROJECT_ROOT"
  exec setsid env \
    ZF_WORKSPACE_HOME="$WORKSPACE_HOME" \
    ZF_WEB_ACTION_TOKEN="$TOKEN" \
    ZF_KANBAN_AGENT_CLAUDE_HEADLESS_CMD="python3 $FAKE_CLAUDE" \
    ZF_KANBAN_AGENT_HEADLESS_TIMEOUT_S=30 \
    PYTHONPATH="$ROOT/src" \
    "$ROOT/.venv/bin/zf" web --host 0.0.0.0 --port "$WEB_PORT" --state-dir "$STATE_DIR"
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
  tail -100 "$WEB_LOG" >&2 || true
  exit 1
fi

# Keep this suite scoped to Kanban Agent. First-install onboarding is covered by
# its own browser suite, so suppress it through the same token-gated Web action
# boundary an operator uses instead of mutating onboarding.json directly.
curl -fsS -X POST "http://127.0.0.1:$WEB_PORT/api/workspace/onboarding" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  --data '{"action":"skip"}' >/dev/null

echo "[playwright] image=$DOCKER_IMAGE"
docker run --rm --network host \
  --user "$(id -u):$(id -g)" \
  --entrypoint bash \
  -v "$ROOT:/work" \
  -v "$RUN_ROOT:/zf-run" \
  -v "$EVIDENCE_DIR:/zf-evidence" \
  -w /work/web \
  -e HOME=/tmp/zf-playwright-home \
  -e PLAYWRIGHT_BROWSERS_PATH=0 \
  -e ZF_PLAYWRIGHT_INSTALL_TIMEOUT_S="$PLAYWRIGHT_INSTALL_TIMEOUT_S" \
  -e ZF_WEB_BASE_URL="http://127.0.0.1:$WEB_PORT" \
  -e ZF_WEB_ACTION_TOKEN_FOR_TEST="$TOKEN" \
  -e ZF_PLAYWRIGHT_EVIDENCE_DIR=/zf-evidence \
  "$DOCKER_IMAGE" \
  -lc 'set -euo pipefail; mkdir -p "$HOME"; chromium_path="$(find /ms-playwright -type f -path "*/chrome-linux64/chrome" 2>/dev/null | sort | tail -1 || true)"; if [[ -n "$chromium_path" ]]; then export ZF_E2E_CHROMIUM_EXECUTABLE_PATH="$chromium_path"; else timeout "${ZF_PLAYWRIGHT_INSTALL_TIMEOUT_S}s" ./node_modules/.bin/playwright install chromium; fi; ./node_modules/.bin/playwright test tests/kanban-agent-conversation.spec.ts --config playwright.config.ts --project=chromium --workers=1 --reporter=line --output=/zf-run/test-results'

echo "[pass] Kanban Agent deterministic browser E2E"
