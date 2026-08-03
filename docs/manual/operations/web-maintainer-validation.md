# Web 维护与 E2E 验证

[English](web-maintainer-validation.en.md) · [运维索引](README.md)

> 面向维护 ZaoFu Web/API、投影和真实 Provider 验证的开发者。普通用户操作见
> [Web Dashboard 使用指南](../06-web-observability-e2e.md)。

## 1. 维护者启动方式

```bash
uv sync --extra dev --extra web
npm --prefix web ci
tools/start-webkanban.sh --host 0.0.0.0 --port 5175
```

临时 simulation 使用 `8002+`，真实 dev session 保留 `8001`。远程监听只用于可信网络。
验证其他 worktree/state 时明确传入 `--state-dir`，避免把旧 checkout 或真实 `.zf` 当测试夹具。

## 2. Docker Playwright

按仓库规则使用 `mcp/playwright:latest`，不要在宿主安装浏览器：

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

前置条件：Web/API 监听 `0.0.0.0:5175`；Docker 支持 host networking；当前目录是 repo root；
`web/node_modules` 已准备。核对 desktop/mobile viewport、console/network error、stream recovery、
projection freshness 和真实可见数据，不只检查页面能打开。

## 3. Scripted E2E

不调用真实 Provider，用于 Kernel/pipeline 的确定性回归：

```bash
uv run python -m tests.e2e.robustness_suite --smoke
uv run python -m tests.e2e.robustness_suite
uv run pytest \
  tests/e2e/test_scripted_runner.py \
  tests/e2e/test_robustness_suite.py \
  tests/e2e/test_w5_phase_report.py \
  -q --no-cov
```

## 4. 真实 Provider Smoke

真实 Codex smoke 会启动 Provider、tmux 和 Worker，并消耗预算。先确认 CLI/version、登录状态、
session 目录、配置 validation、预算、超时和隔离 state dir。

```bash
uv run python -m tests.e2e.robustness_suite \
  --skip-unit \
  --skip-dry-run \
  --include-real codex \
  --confirm-real
```

更低层入口：

```bash
uv run python -m tests.e2e.run_mixed \
  --worktree /tmp/zf-codex-smoke \
  --config examples/dev-codex-backends.yaml \
  --seed-file tests/e2e/seeds/large_dev_split_3_tasks.txt \
  --expected-done 1 \
  --timeout 1800 \
  --confirm
```

真实 Provider 结果不能由 scripted/mock 测试代替。失败时保留 events、provider transcript、usage、
git evidence 和 projection diagnostics，再清理 tmux/process/state。

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

或使用：

```bash
tests/e2e/run_real_state_web_validation.sh \
  /tmp/zf-codex-smoke/.zf \
  /tmp/zf-full
```

重点核对 `matrix`、`fanout_trace_chain`、`codex_hook_usage` 和 `summary.failed`。
`--require-real-codex` 必须在缺少真实 CLI/session/usage 证据时 fail closed。

## 6. 归档与分层验证

```bash
uv run zf archive-run \
  --run-id "run-$(date -u +%Y%m%d%H%M%S)" \
  --live-state-dir /tmp/zf-codex-smoke/.zf \
  --status passed
uv run zf runs list
uv run zf runs rebuild
```

`runs list` 会先增量刷新 workflow/stage/attempt projection 再读取；`runs rebuild` 执行完整
重建。两者都不应改写 canonical events/Task，但不是 state dir 字节级零写入命令。

| 层级 | 目标 |
|---|---|
| L0 | config/schema/skill/topology 静态检查 |
| L1 | deterministic unit/integration |
| L2 | scripted 完整流程 |
| L3 | 单 Provider 真实 smoke |
| L4 | 多 Worker、压力与恢复 |
| L5 | Web/API projection、Playwright 与人工证据 |

按 L0 -> L5 递进。不要跳过确定性层直接消耗真实 Provider；也不要把 full pytest 中的宿主
capability/version sensor 当作真实 Provider E2E 证明。

## 7. 清理

临时运行使用 `/tmp/zf-<purpose>-<utc-timestamp>/`。结束后写入 simulation completion 证据，
终止对应 tmux/Web 进程并删除临时 state。不要清理真实 Project 的 configured state dir。
