# ZaoFu 快速开始

> 适用对象：第一次安装 ZaoFu，并希望通过 Web 创建/打开 Project，再使用
> Kanban Agent、Channel、Research 或交付 Workflow 的操作者。
>
> 当前路径按 CLI、Web 和真实浏览器 E2E 核实于 2026-07-29。

## 0. 安装并启动 Dashboard

必需环境：

- Python 3.11+、`uv`、Git、`tmux`；
- 至少一个已安装并登录的 provider CLI：Codex 或 Claude Code；
- 使用 Claude Code stream-json transport 时安装 `stream-json` extra。

```bash
cd /path/to/zaofu
uv sync --extra dev --extra web --extra stream-json
uv run zf --version
uv run zf doctor provider --backend codex
```

启动 Workspace Dashboard：

```bash
export ZAOFU_ROOT=/path/to/zaofu
export ZF_WEB_ACTION_TOKEN="$(openssl rand -hex 24)"

uv run --project "$ZAOFU_ROOT" zf web \
  --host 127.0.0.1 \
  --port 8001 \
  --workspace-only
```

浏览器访问 `http://127.0.0.1:8001/`。只有在可信网络中才绑定 `0.0.0.0`。

## 1. 完成安装 Onboarding

首次进入按四步完成安装级设置：

1. **Provider**：选择 Codex 或 Claude Code 作为 primary provider；两者都可用时可启用
   Mixed team，由另一个 provider 承担独立 verify lane。
2. **Environment**：检查宿主依赖和 provider 可用性。
3. **Access**：为当前浏览器建立受控 action session。
4. **Ready**：进入 Workspace。

Onboarding 不创建 Project。零 Project 的空 Workspace 是正常状态。

## 2. Add/Open Project

点击左侧 Project 选择器旁的 `+`：

1. 输入**服务端本机**上的 Project path。
2. 点击 `Inspect`。
3. 审核服务端返回的唯一 admission action 与 diagnostics。
4. 仅在 `initialize_project` 时填写 Project Name、Project Brief、Project Stack、
   Primary Provider 和可选 Mixed team。
5. 执行界面给出的 `Open Project`、`Add & Open`、`Initialize & Open` 或
   `Create Project`。

Inspect 会按磁盘真相选择动作：

| 目录状态 | 行为 |
|---|---|
| 已注册且健康 | 直接打开 |
| 有合法 `zf.yaml`、未注册 | 注册并打开 |
| 有合法配置、缺运行态 | 初始化 state 后打开 |
| 没有 `zf.yaml` | 创建默认 multi-kind Project |
| 配置无效或存在残缺非空 state | `blocked`，先修复，不猜测覆盖 |

![Add/Open Project 创建表单](assets/project-add-open-current.png)

目标路径不存在或为空时，`Create Project` 会生成最小 README/src/tests、独立 Git
repository 和 initial HEAD，使默认 worktree runtime 可以启动。已有非空代码目录不会
被 Web 自动 `git init` 或提交；需要先由 operator 建立可信 Git baseline。

Project Brief 应写长期背景、目标和关键约束，不要写单次 Task Prompt。初始化后：

- `project.description` 保存到 `zf.yaml`；
- Project Context 托管段写入 `AGENTS.md`；
- Stack 及探测到的 build/test 命令写入独立 Profile 托管段；
- `CLAUDE.md` 保持 Claude 专属规则，并引用 `AGENTS.md`；
- Project 注册到 Workspace，但不会自动创建 Task 或启动 Workflow。

已有注册 Project 不需要重新导入；刷新 Workspace 后直接打开即可。

## 3. `zf.yaml` 仍是唯一控制面

Add/Open Project 不再让用户选择 YAML、preset、Controller、kind、scale、lane 或 role。
这只是把配置选择移出日常创建表单：

- 已有合法 `zf.yaml` 原样保留；
- 新目录生成一份默认 multi-kind `zf.yaml`；
- Stack 只决定项目指令和命令 Profile，不决定 Workflow；
- Provider 选择编译 provider policy；Mixed team 仍保留一个 primary backend，
  另一个 provider 用于独立验证，不存在 `backend: mixed`；
- Kanban Agent 只能从当前 `zf.yaml` 展开的 active route catalog 推荐 Workflow。

显式选择单一 Controller、迁移控制面或物化 Bootstrap 推荐时，才使用
`zf profile bootstrap`。不要为每条 PRD、Issue 或 Refactor 创建第二份 `zf.yaml`。

## 4. 打开 Project 后如何输入需求

Kanban Agent 是 Project 内的通用 Coding Agent，不只是看板监工。根据需求和你的明确
意图，它可以走不同路径：

| 目标 | 是否先要 Task | 交互与结果 |
|---|---:|---|
| 普通分析、修改代码、运行测试 | 否 | 在当前 provider session 内直接工作，受权限和 Git 规则约束 |
| 只建立可追踪工作项 | 否 | 生成 `Create Task` proposal，确认后创建 Task |
| 多角色澄清、评审或讨论 | 否 | 给出 Channel setup Plan，选择后自动建 Channel 并开始讨论 |
| 固定角色深度研究 | 是 | 对已有 Task 给出 Research route Plan，随后独立 Approve |
| PRD/Issue/Refactor/Planning 交付 | 是 | 对已有 Task 推荐 active Workflow route，随后独立 Approve |

不要在创建 Project 时提前决定 lane 数或角色。Kanban Agent 应基于具体 Task 的业务类型、
复杂度和验收目标，从 active catalog 中推荐单 lane、多 lane、Research 或其他已注册
route。

## 5. 创建 Channel Group

产品里常说的 Channel Group，canonical 模型是运行时的 **Channel + Members**，不是
`zf.yaml` 中的静态配置块。向 Kanban Agent 描述需求并明确希望多角色讨论，例如：

```text
为这个需求创建一个 PRD 澄清 Channel，重点讨论安全边界、技术可行性和验收标准。
```

Kanban Agent 返回一个 Plan，选项会显示模板、成员角色、成员数和讨论轮次：

![Channel setup Plan](assets/kanban-channel-plan.png)

选择方案并点击 `Create & start` 后，系统一次完成：

```text
创建 Channel
-> 物化模板成员、角色上下文、技能与写权限
-> 把原始需求发到 Channel
-> 启动 fanout_then_synthesis 讨论
-> 默认 responder/synthesizer 收敛结论
```

不需要再手工建成员或复制第一条消息。`Chat about` 会保留 Plan，允许先补充轮次、
角色或范围。讨论结束后，人可以继续在同一 Channel 输入新问题或延续原需求。

Channel 独立于 Workflow：讨论结论不会自动创建 Task，也不会自动点火 Research/交付
Workflow。需要进入交付时，让 Kanban Agent 基于结论生成 `Create Task` proposal，
确认 Task 后再选择 Workflow。

完整模板与飞书用法见
[15 Channel 协作使用手册](15-channel-collaboration.md)。

## 6. 启动 Research 或 Task Workflow

Research 和交付 Workflow 共用一套 Task-bound start service：

```text
已有 Task
-> Kanban Agent 读取 zf workflow routes
-> Plan 推荐 active route / 参数 / topology / roles
-> 选择方案
-> 生成独立 Approve 卡
-> Owner 确认
-> workflow.invoke.requested
```

Plan 负责澄清和选择，不等于授权：

![Task-bound Workflow Plan](assets/kanban-task-workflow-plan.png)

选择后仍需确认 exact Task、route、objective 和参数：

![Workflow Approve](assets/kanban-task-workflow-approve.png)

Research 的默认固定 route 是 `research:fixed`，角色为
`source_researcher`、`product_analyst`、`technical_analyst`、`risk_critic` 和
`synthesizer`。它产出研究摘要、证据引用、开放问题和 PRD/Refactor prompt inputs；
不会自动创建交付 Task 或直接执行 PRD 拆分。

`research-review` Channel 模板只是多角色讨论/评审，不等于启动
`research:fixed`。只有用户明确要求 Research fanout、已有 Task 且当前 Project
route catalog 提供该 route 时，才进入 Research Workflow。

CLI 可以检查同一套 surface-neutral route：

```bash
cd /path/to/project
uv run --project "$ZAOFU_ROOT" zf workflow routes \
  --task TASK-ID \
  --format json
```

提案与授权命令见
[20 Project 创建、Bootstrap 与 Workflow 点火](20-project-bootstrap-workflow-ignition.md)。

## 7. 启动 Runtime 与观测

创建 Project 不会凭空运行 Workflow。需要真实 worker 时，从 Project 根启动：

```bash
cd /path/to/project
uv run --project "$ZAOFU_ROOT" zf validate --cold-start
uv run --project "$ZAOFU_ROOT" zf start
```

另一个终端观测：

```bash
uv run --project "$ZAOFU_ROOT" zf status --workers
uv run --project "$ZAOFU_ROOT" zf kanban --board
uv run --project "$ZAOFU_ROOT" zf events --last 30
```

`zf start` 只启动 worker、sidecar 和 watcher。没有已批准的
`workflow.invoke.requested` 时，worker idle 是正确状态。

## 8. CLI 创建 Project

Web greenfield、`zf project init` 和 `tools/init-project.sh` 共同复用
`init_flow_project` 的 Project 初始化语义。Web 不执行 Shell。
CLI 示例：

```bash
uv run --project "$ZAOFU_ROOT" zf project init \
  --name account-service \
  --description "账号与认证服务；目标是逐步统一登录和安全策略。" \
  --root /path/to/account-service \
  --create \
  --git-init \
  --backend codex \
  --verify-backend claude-code \
  --stack python \
  --workspace-register
```

需要同时完成 Git readiness、`zf init`、validate 和 startup dry-run 时：

```bash
tools/init-project.sh \
  --project-dir /path/to/account-service \
  --name account-service \
  --description "账号与认证服务" \
  --backend codex \
  --verify-backend claude-code \
  --stack python \
  --yes
```

两条命令默认都只创建 Project，不创建 Task，不点火 Workflow。

## 9. 停止

```bash
uv run --project "$ZAOFU_ROOT" zf stop
```

只有优雅停止失败时才使用 `zf stop --force`。共享主机不要执行
`tmux kill-server`。

## 下一步

- [Project 创建、Bootstrap 与 Workflow 点火](20-project-bootstrap-workflow-ignition.md)
- [`zf.yaml` 控制面与运行态](02-zf-yaml-control-plane.md)
- [Channel 协作](15-channel-collaboration.md)
- [Web、观测与 E2E](06-web-observability-e2e.md)
- [故障排查](07-troubleshooting.md)
