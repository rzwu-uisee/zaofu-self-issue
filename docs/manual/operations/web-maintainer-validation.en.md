# Web Maintenance and E2E Validation

[中文](web-maintainer-validation.md) · [Operations index](README.en.md)

> For maintainers of ZaoFu Web/API, projections, and real-provider validation. User operations are in the
> [Web Dashboard User Guide](../06-web-observability-e2e.en.md).

## 1. Maintainer Startup

```bash
uv sync --extra dev --extra web
npm --prefix web ci
tools/start-webkanban.sh --host 0.0.0.0 --port 5175
```

Use ports `8002+` for temporary simulations and reserve `8001` for the real dev session. Expose a remote
listener only on a trusted network. Pass `--state-dir` explicitly when validating another worktree/state so
a stale checkout or real `.zf` does not become the fixture.

## 2. Docker Playwright

Use `mcp/playwright:latest`; do not install host browsers:

```bash
docker run --rm --network host \
  --user "$(id -u):$(id -g)" \
  --entrypoint bash \
  -v "$PWD:/work" \
  -w /work/web \
  -e PLAYWRIGHT_BROWSERS_PATH=0 \
  -e ZF_WEB_BASE_URL=http://127.0.0.1:5175 \
  mcp/playwright:latest \
  -lc 'set -euo pipefail; timeout 180s ./node_modules/.bin/playwright install chromium; ./node_modules/.bin/playwright test --project=chromium --workers=1'
```

Prerequisites: Web/API listens on `0.0.0.0:5175`; Docker supports host networking; the current directory is
the repo root; and `web/node_modules` exists. Check desktop/mobile viewports, console/network errors, stream
recovery, projection freshness, and visible real data rather than only page reachability.

## 3. Scripted E2E

These paths avoid a real provider and exercise deterministic Kernel/pipeline behavior:

```bash
uv run python -m tests.e2e.robustness_suite --smoke
uv run python -m tests.e2e.robustness_suite
uv run pytest \
  tests/e2e/test_scripted_runner.py \
  tests/e2e/test_robustness_suite.py \
  tests/e2e/test_w5_phase_report.py \
  -q --no-cov
```

## 4. Real-provider Smoke

A real Codex smoke starts the provider, tmux, and Workers and consumes budget. Verify CLI/version, login,
session directory, config validation, budget, timeout, and isolated state first.

```bash
uv run python -m tests.e2e.robustness_suite \
  --skip-unit \
  --skip-dry-run \
  --include-real codex \
  --confirm-real
```

Lower-level entry point:

```bash
uv run python -m tests.e2e.run_mixed \
  --worktree /tmp/zf-codex-smoke \
  --config examples/dev-codex-backends.yaml \
  --seed-file tests/e2e/seeds/large_dev_split_3_tasks.txt \
  --expected-done 1 \
  --timeout 1800 \
  --confirm
```

Scripted/mock tests do not prove real-provider behavior. On failure, retain events, provider transcripts,
usage, Git evidence, and projection diagnostics before cleaning tmux/process/state.

## 5. Full-stack Scorecard

```bash
PYTHONPATH=src python -m tests.e2e.full_stack_validation \
  --state-dir /tmp/zf-codex-smoke/.zf \
  --repo-root "$PWD" \
  --require-real-codex \
  --require-docker \
  --preflight-output /tmp/zf-full/preflight.json \
  --output /tmp/zf-full/scorecard.json \
  --markdown /tmp/zf-full/report.md
```

Or run:

```bash
tests/e2e/run_real_state_web_validation.sh \
  /tmp/zf-codex-smoke/.zf \
  /tmp/zf-full
```

Inspect `matrix`, `fanout_trace_chain`, `codex_hook_usage`, and `summary.failed`. With
`--require-real-codex`, missing real CLI/session/usage evidence must fail closed.

## 6. Archive and Validation Tiers

```bash
uv run zf archive-run \
  --run-id "run-$(date -u +%Y%m%d%H%M%S)" \
  --live-state-dir /tmp/zf-codex-smoke/.zf \
  --status passed
uv run zf runs list
uv run zf runs rebuild
```

`runs list` incrementally refreshes workflow, stage, and attempt projections
before reading; `runs rebuild` performs a full rebuild. Neither should rewrite
canonical events or Tasks, but they are not byte-for-byte no-write operations
on the state directory.

| Tier | Goal |
|---|---|
| L0 | config/schema/skill/topology static checks |
| L1 | deterministic unit/integration |
| L2 | scripted full flow |
| L3 | single-provider real smoke |
| L4 | multi-Worker load and recovery |
| L5 | Web/API projection, Playwright, and human evidence |

Progress from L0 to L5. Do not spend real-provider budget before deterministic tiers pass, and do not treat
host capability/version sensors in full pytest as real-provider E2E proof.

## 7. Cleanup

Use `/tmp/zf-<purpose>-<utc-timestamp>/` for temporary runs. Record simulation completion, stop the matching
tmux/Web processes, and remove temporary state. Never clean a real Project's configured state directory.
