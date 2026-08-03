# 首个可验证交付

[English](first-verified-delivery.en.md) · [入门索引](README.md)

> 目标：从一个 source checkout 和目标代码库出发，创建一个真实 Task，经人工批准启动
> Workflow，并从 Delivery 看到运行、证据和终态。完整分支路线仍见
> [01 快速开始](../01-quickstart.md)。

## 1. 准备

需要 Python 3.11+、`uv`、Git、`tmux`，以及已经登录的 Codex CLI 或 Claude Code CLI。

```bash
git clone https://github.com/uisee-ai/zaofu /path/to/zaofu
cd /path/to/zaofu
uv sync --extra dev --extra web --extra stream-json
uv run zf --version
```

启动 Workspace Dashboard：

```bash
export ZAOFU_ROOT=/path/to/zaofu
"$ZAOFU_ROOT/tools/start-webkanban.sh" \
  --host 127.0.0.1 \
  --port 8001 \
  --workspace-only
```

打开 `http://127.0.0.1:8001/`。只在可信网络中绑定 `0.0.0.0`。

## 2. 建立 Project

1. 完成首次 Bootstrap。
2. 选择 `Add Project`，输入目标代码库的真实绝对路径。
3. 审核 Project Name、Brief、Stack、Primary Provider 和 Mixed Team。
4. 执行 Initialize，再打开 Project。

这一步只创建 Project 容器、`zf.yaml` 和 state dir，不创建 Task，也不启动 Workflow。

## 3. 启动 Runtime

在目标 Project 根目录执行：

```bash
cd /path/to/project
uv run --project "$ZAOFU_ROOT" zf validate --cold-start
uv run --project "$ZAOFU_ROOT" zf start
```

`validate` 会更新 state dir 下的 last-known-good 和 validation report 缓存，但不改
Task、Run 或事件事实。

没有已批准 Workflow 时 worker 显示 idle 是正确状态。

## 4. 创建 Task 并批准 Workflow

打开 Kanban Agent，输入一条有目标和验收结果的请求：

```text
为登录失败审计增加结构化错误原因和回归测试。
先创建一个可追踪 Task，再为它推荐合适的交付 Workflow；启动前让我确认。
```

产品路径固定为：

```text
Create Task proposal
  -> 人确认 Task
  -> Task-bound Workflow Plan
  -> 选择 route，或 Chat about / Customize
  -> exact Workflow proposal
  -> 独立 Approve
  -> workflow.invoke.requested
```

Plan 只固定选择，不代表授权。只有最后的 `Start workflow` 才启动 Run。

![playgroud 中从 Task proposal、Workflow Plan 到独立批准和点火](../assets/quickstart-direct-workflow.webp)

## 5. 观察和签收

在 Web 中按以下顺序检查：

1. `Tasks`：Task 状态、owner、contract 和当前 stage。
2. `Delivery -> Runs`：本轮 stage、attempt、重试和 causation。
3. `Delivery -> Delivery Map -> Coverage`：每个 mandatory Claim 是否有 Task 和证据覆盖。
4. `Delivery -> Delivery Map -> Work`：Goal 到 Claim、Task、Try 和 Result 的关系。
5. `Monitoring -> Runs`：Run 终态后的 Goal Dossier。
6. `Inbox`：需要批准、处理的 blocker 和 owner-visible delivery。

终端可以核对同一事实：

```bash
uv run --project "$ZAOFU_ROOT" zf kanban --board
uv run --project "$ZAOFU_ROOT" zf task trace TASK-ID
uv run --project "$ZAOFU_ROOT" zf events --last 50
```

## 完成定义

首次交付完成需要同时满足：

- Task 已绑定批准的 Workflow 和 Run；
- Impl/Verify 的结果和 evidence refs 可追溯；
- mandatory Claims 已闭合，或 blocker/next action 明确；
- Run 收敛为 completed、blocked、failed 或 cancelled，而不是长期停在 active；
- completed 时 Goal Dossier 与 owner receipt 一致。

Agent 最后一段“已完成”不属于以上任一机械条件。

## 停止

```bash
uv run --project "$ZAOFU_ROOT" zf stop
```

共享主机不要执行 `tmux kill-server`。
