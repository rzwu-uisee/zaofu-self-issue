#!/usr/bin/env bash

set -euo pipefail

ROOT="$(git rev-parse --show-toplevel)"
COMMON_GIT_DIR="$(git rev-parse --path-format=absolute --git-common-dir)"
COMMON_ROOT="$(dirname "$COMMON_GIT_DIR")"
NODE_MODULES="${ZF_E2E_NODE_MODULES:-$COMMON_ROOT/web/node_modules}"
DOCKER_IMAGE="${ZF_PLAYWRIGHT_IMAGE:-mcp/playwright:latest}"
CHROMIUM_PATH="${ZF_PLAYWRIGHT_CHROMIUM_PATH:-/ms-playwright/chromium-1222/chrome-linux64/chrome}"
BASE_URL=""
PROJECT_ID=""
TASK_ID=""
RUN_ID=""
EVIDENCE_DIR=""
CAPTURE_NAME="terminal-failure"

usage() {
  printf '%s\n' \
    "Usage: capture_five_workflow_terminal.sh [options]" \
    "" \
    "Required environment:" \
    "  ZF_WEB_ACTION_TOKEN_FOR_TEST   Web action token; never passed in argv" \
    "" \
    "Options:" \
    "  --base-url URL       Running Web dashboard URL" \
    "  --project-id ID      Isolated Project id" \
    "  --task-id ID         Exact case Task id" \
    "  --run-id ID          Exact Workflow run id" \
    "  --evidence-dir PATH  Host output directory" \
    "  --capture-name NAME  Screenshot/evidence prefix"
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --base-url) BASE_URL="$2"; shift 2 ;;
    --project-id) PROJECT_ID="$2"; shift 2 ;;
    --task-id) TASK_ID="$2"; shift 2 ;;
    --run-id) RUN_ID="$2"; shift 2 ;;
    --evidence-dir) EVIDENCE_DIR="$2"; shift 2 ;;
    --capture-name) CAPTURE_NAME="$2"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) printf 'unknown option: %s\n' "$1" >&2; usage >&2; exit 2 ;;
  esac
done

if [[ -z "$BASE_URL" || -z "$EVIDENCE_DIR" ]]; then
  printf '%s\n' '--base-url and --evidence-dir are required' >&2
  exit 2
fi
if [[ -z "${ZF_WEB_ACTION_TOKEN_FOR_TEST:-}" ]]; then
  printf '%s\n' 'ZF_WEB_ACTION_TOKEN_FOR_TEST is required' >&2
  exit 2
fi
if [[ ! -x "$NODE_MODULES/.bin/playwright" ]]; then
  printf 'missing Playwright dependencies: %s\n' "$NODE_MODULES" >&2
  exit 2
fi
if ! docker image inspect "$DOCKER_IMAGE" >/dev/null 2>&1; then
  printf 'Playwright image is not local: %s\n' "$DOCKER_IMAGE" >&2
  exit 2
fi

mkdir -p "$EVIDENCE_DIR"
EVIDENCE_DIR="$(cd "$EVIDENCE_DIR" && pwd)"

docker run --rm --network host \
  --user "$(id -u):$(id -g)" \
  --entrypoint bash \
  -v "$ROOT:/work:ro" \
  -v "$NODE_MODULES:/work/web/node_modules:ro" \
  -v "$EVIDENCE_DIR:/zf-evidence" \
  -w /work/web \
  -e HOME=/tmp/zf-playwright-home \
  -e ZF_WEB_BASE_URL="$BASE_URL" \
  -e ZF_WEB_ACTION_TOKEN_FOR_TEST \
  -e ZF_FIVE_E2E_PROJECT_ID="$PROJECT_ID" \
  -e ZF_FIVE_E2E_TASK_ID="$TASK_ID" \
  -e ZF_FIVE_E2E_RUN_ID="$RUN_ID" \
  -e ZF_PLAYWRIGHT_EVIDENCE_DIR=/zf-evidence \
  -e ZF_PLAYWRIGHT_CAPTURE_NAME="$CAPTURE_NAME" \
  -e ZF_E2E_CHROMIUM_EXECUTABLE_PATH="$CHROMIUM_PATH" \
  "$DOCKER_IMAGE" \
  -lc 'set -euo pipefail; mkdir -p "$HOME"; ./node_modules/.bin/playwright test tests/five-workflow-terminal-capture.spec.ts --config playwright.config.ts --project=chromium --workers=1 --reporter=line --output=/tmp/zf-playwright-results'
