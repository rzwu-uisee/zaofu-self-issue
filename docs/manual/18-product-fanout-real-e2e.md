# Product Fanout 与五类 Workflow 真实 E2E

[English](18-product-fanout-real-e2e.en.md) · [Plan、Task Map 与调度](13-plan-task-map-orchestrator-dispatch.md)

> 面向维护者和 QA。当前 runner、Research generation fence 和确定性测试已经实现；
> **真实 Provider 5/5 仍待独立签收**。Task Pipeline v4 实现完成但默认关闭，rollout
> 仍是 NO-GO。本手册不会把 mock、入口 turn 或 UI toast 当作真实交付证明。

## 1. 验证对象

需要分别证明五个 Workflow family：

| Family | 当前验证重点 | 成功终态 |
|---|---|---|
| PRD | Plan Artifact、Task Map、Impl/Verify、Candidate、Goal Closure | exact Run 的 `run.goal.completed`，并等待适用 Task `done` |
| Issue | 需求归类、修复 Task、回归证据、Candidate | 同上 |
| Refactor | source/target root、parity、Candidate、Goal Closure | 同上 |
| General | static-safe route、artifact delivery、closure policy | exact Run 的 `run.goal.completed` |
| Research | immutable generation、reader aggregate、research report lineage | aggregate completed + `workflow.result.available(research_report)` + Task `done` |

`workflow.invoke.requested`、`fanout.started`、Provider turn 结束、Agent 自述和页面上的绿色提示
都不是成功终态。

## 2. 隔离与前置条件

每个验证 Project 必须满足：

- ZaoFu implementation 是 immutable、clean commit；
- Project seed 是 clean commit；
- 使用唯一 `project.state_dir`、tmux session、branch prefix 和 Web port；
- state 中没有旧 Task、invoke、admission、dispatch 或 fanout 历史；
- active route catalog 暴露本次 family，并绑定预期真实 Provider；
- 使用本地 `mcp/playwright:latest`，浏览器验证走 Docker host network；
- 真实运行期间不热改 ZaoFu 代码、`zf.yaml`、route 或已冻结 Task contract。

临时目录使用 `/tmp/zf-<purpose>-<utc-timestamp>/`。端口 `8001` 留给真实 dev dashboard，
临时 Web 使用 `8002+`。

## 3. Suite Preflight

repo-owned runner 先冻结 implementation、Project、effective config、route catalog 和 Provider
identity；任何业务 Task/Workflow 创建前先执行：

```bash
PYTHONPATH=src uv run python tests/e2e/five_workflow_terminal_runner.py \
  suite-preflight \
  --project-root "$PROJECT_ROOT" \
  --state-dir "$STATE_DIR" \
  --config "$PROJECT_ROOT/zf.yaml" \
  --implementation-root "$ZAOFU_ROOT" \
  --require-backend codex \
  --check-host \
  --playwright-image mcp/playwright:latest \
  --out "$REPORT_ROOT/preflight/suite-manifest.json"
```

失败时停止，不要通过复用旧 state、关闭 identity check 或修改报告文件绕过。

## 4. 冻结 Case 并启动

Task 已创建、Workflow 尚未批准时冻结 exact case：

```bash
PYTHONPATH=src uv run python tests/e2e/five_workflow_terminal_runner.py \
  prepare-case \
  --suite-manifest "$REPORT_ROOT/preflight/suite-manifest.json" \
  --family issue \
  --task-id "$TASK_ID" \
  --route-id "$ROUTE_ID" \
  --out "$REPORT_ROOT/case-issue/case-manifest.json"
```

Refactor 还必须传互不相同、互不嵌套且 symlink 解析后不重叠的
`--source-root` / `--target-root`。Case freeze 后，通过 Kanban Agent、Web 或
`zf workflow start` 的正常 Plan/Approve 路径启动一次 Workflow；同一 case 第二个
`workflow.invoke.requested` 直接判 FAIL。

Research start 会把 prompt、effective config、route/template、role、Task contract 和 Run
Contract 绑定为 immutable `workflow_generation`。配置漂移或 restart 时，旧 generation 先
`workflow.generation.superseded` / `run.cancelled`，不得继续派发 reader。

## 5. 等待真实终态

```bash
PYTHONPATH=src uv run python tests/e2e/five_workflow_terminal_runner.py \
  wait \
  --case-manifest "$REPORT_ROOT/case-issue/case-manifest.json" \
  --timeout 900 \
  --poll 1 \
  --evidence-dir "$REPORT_ROOT/case-issue/terminal"
```

`900` 秒是上限，不是固定 sleep。runner 每轮先检查 frozen identity，再按事件顺序识别第一个
terminal。成功后的晚到诊断噪声不反转结果；旧 generation 的 cancellation 也不能因复用
Task ID 污染当前 Run。

首个失败、identity drift 或超时会保存 Task、Run admission、WorkflowOperation、TaskAttempt、
RoleSession、相关事件、artifact refs 和 diagnostics。需要浏览器证据时，用
`--screenshot-argv-json` 传 repo-owned
`tests/e2e/scripts/capture_five_workflow_terminal.sh` argv；action token 只能通过环境继承，不能
写进 argv 或报告。

## 6. v3/v4 与并行验证

### 6.1 Task Pipeline v4 canary

PRD/Issue/Refactor 的 v4 profile 位于：

```text
examples/prod/controller/issue-task-pipeline-v4-canary*.yaml
examples/prod/controller/prd-task-pipeline-v4-canary*.yaml
examples/prod/controller/refactor-task-pipeline-v4-canary*.yaml
```

它们均为 `preferred: false`，默认 `ZF_TASK_PIPELINE_MODE=shadow`。测试 blocking 时必须显式
设置环境，并从 effective config 回读：resident orchestrator、on-demand coding roles、
`task_pipeline.mode=blocking`、frozen exact Candidate 和 partial auto-ship fence。

公平 A/B 使用预注册 manifest，且每个 arm 使用独立 clean worktree/state：

```bash
PYTHONPATH=src uv run python tests/e2e/task_pipeline_v4_canary.py \
  --repo "$ZAOFU_ROOT" \
  --registration "$REGISTRATION_JSON" \
  --output-dir "$REPORT_ROOT/task-pipeline-ab" \
  --dry-run
```

真实执行还需传无 shell 的 `--command-json` argv 模板。只有 provider identity、预算、输入、
Task Map 和 conditional role 都一致，且 v4 无 false completion/terminal residual，A/B 才有效。
即使报告为 `CANARY_EXPAND`，也不会自动把 v4 设为默认。

### 6.2 四 Project 并行 Kanban suite

完成串行验证后，才可用 `tests/e2e/kanban_parallel_suite.py` 并行 General、Issue、PRD、
Refactor。每个 case 必须拥有不同 Project root/state dir；General 使用 v3，另外三类使用对应
v4 blocking profile。coordinator 只编排外部 driver、终态 observer、一次 bounded recovery 和
cleanup，不创建 Task、不发业务事件，也不是第二 scheduler。

```bash
PYTHONPATH=src uv run python tests/e2e/kanban_parallel_suite.py \
  --manifest "$REPORT_ROOT/parallel-suite.json" \
  --report "$REPORT_ROOT/parallel-report.json"
```

并行通过不能替代 Research，也不能替代先行的串行 5/5。

## 7. 签收清单

- suite/case identity 全程未漂移；
- exact Run 到达 family 对应成功终态，Task projection 在适用时收敛为 `done`；
- 没有 duplicate invoke、stale generation dispatch 或旧 Run terminal 串线；
- Task Pipeline v4 的 Impl/Verify/Integration receipt、Candidate freeze 和全局签收目标一致；
- required artifacts、command receipts、target commit、Goal Claims 和 Dossier 可回读；
- Kanban、Delivery、Trace、Graph、Loop 与 Event/Store/Artifact 事实一致；
- 无 `event.schema.violated`、未解释 blocker、false completion 或 terminal residual；
- ZaoFu implementation checkout 在验证前后保持同一 clean commit。

当前确定性 runner/fence 测试通过，只证明测试工具和机械合同；真实 Provider 5/5 完成前，
不得写成产品级全流程签收。

## 8. 清理

每个模拟先发 `simulation.done`，再从对应 Project 目录停止：

```bash
uv run zf emit simulation.done --payload '{"source":"five-workflow-e2e"}'
uv run zf stop
```

只停止本 case 的 tmux/Web/provider 进程。删除 Git worktree 前先检查：

```bash
git -C "$WORKTREE" status --short --untracked-files=all
```

只有 clean worktree 才执行标准删除：

```bash
git worktree remove "$WORKTREE"
git worktree prune
```

dirty worktree 必须先 commit、stash 或归档并复核，不使用
`git worktree remove --force`、`git branch -D`、全局 tmux kill 或无范围的目录删除。
