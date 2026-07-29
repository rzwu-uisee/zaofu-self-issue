# Channel 协作使用手册

> 适用对象：希望让多个 agent 围绕一个需求协作，并在 Web 或飞书中继续对话的操作者。
>
> 状态：Kanban Plan 自动建 Channel、模板成员、原始需求投递、
> `fanout_then_synthesis` 与继续对话已实现；本手册按 2026-07-28 真实 E2E 更新。

## 1. Channel Group 的当前模型

产品交互可以称为 Channel Group，kernel canonical 模型是：

```text
Channel
├── Members (provider agent / runtime role / human / observer)
├── Messages and threads
├── Discussion policy
├── Writer role and writer scope
└── Synthesis artifacts and event refs
```

Channel 是运行时动态对象，由 `channel.created` 及后续 `channel.*` 事件建立，不在
`zf.yaml` 中预声明一个 `channel group` 配置块。`zf.yaml` 仍提供 provider、runtime
role、权限和集成等控制面约束。

Channel 与 Workflow 相互独立：

- 创建或讨论 Channel 不要求先有 Task；
- Channel 用于澄清、评审、辩论和形成共识；
- Channel 结论不会自动创建 Task；
- Channel 不直接启动 Research 或交付 Workflow；
- 需要进入执行时，先人工确认 `Create Task` proposal，再为该 Task 选择 Workflow。

## 2. 推荐入口：让 Kanban Agent 自动建 Channel

不需要先点 `New Channel`、逐个邀请成员或复制第一条需求。在 Kanban Agent 中描述需求
并明确希望协作，例如：

```text
请为登录安全改造创建一个 PRD 澄清 Channel。
需要产品、架构、质疑和安全视角，讨论 12 轮以内并输出可追溯结论。
```

Kanban Agent 应返回一个 action-bound Channel setup Plan。每个选项必须绑定具体：

- `template_id`；
- Channel name（可选）；
- 精确成员角色与成员数；
- provider/model override（可选）；
- `max_rounds` 等预算；
- writer role 与受限 writer scope。

![Channel setup Plan 显示模板、成员与轮次](assets/kanban-channel-plan.png)

选择方案后点击 `Create & start`。这一个动作原子化执行：

```text
channel-create-and-start
-> 创建 Channel
-> 按模板物化 Members、role context、skills 和权限
-> 投递触发本次 Plan 的原始用户需求
-> 启动 discussion
-> fanout blind replies
-> relay / critique
-> synthesis
```

不再生成第二张 Approve 卡。Channel setup 是 Plan 直接应用的受控例外；浏览器 action
session/token 仍必须有效。其他高风险动作继续走独立 Approve。

### `Chat about`

`Chat about` 不执行选项，也不丢弃 Plan。它把补充内容发回同一 Kanban Agent session，
适合调整：

- 讨论轮次；
- 是否启用可选角色；
- 关注范围和预期输出；
- primary provider、模型或预算；
- writer scope。

Agent 应基于补充信息更新 Plan，而不是让用户改 JSON。

## 3. 内置 Channel 模板

当前内置模板如下，均使用 `fanout_then_synthesis`：

| Template | 默认成员 | Writer |
|---|---|---|
| `prd-clarification` | `product_pm`、`arch`、`critic`、`synthesizer`，可选 `security_reviewer` | `product_pm`，默认限 `docs/design/**`、`docs/impl/**` |
| `research-review` | `researcher`、`arch`、`critic`、`synthesizer` | `researcher`，默认限 research artifacts |
| `architecture-review` | `arch`、`security_reviewer`、`dev_reviewer`、`critic` | `arch` |
| `quick-change` | `tech_leader`、`dev_reviewer`、`qa_analyst` | `tech_leader` |
| `incident-triage` | `tech_leader`、`qa_analyst`，可选 `security_reviewer` | `tech_leader` |

模板不是随意角色字符串集合。required role 不能被关闭；可选角色、backend、model、
writer、writer scope 和预算只能通过模板允许的 override 修改。非 writer 默认降为
read-only，避免所有成员同时改 Project。

## 4. 讨论、收敛与继续输入

`fanout_then_synthesis` 分为三段：

1. `phase1_blind`：成员独立回答，避免先入为主；
2. `phase2_relay`：互相转发、质疑和补证据；
3. `phase3_synthesis`：模板 synthesizer/default responder 收敛。

完整事件包括 Channel/Member 创建、消息投递、reply request/start/delta/complete、
discussion phase 和 synthesis refs。可通过 Web、`zf events` 或飞书投影观察。

真实讨论完成后，Channel 保持可交互。人可以在输入框继续追问、补充新需求或要求重开
讨论，不需要重建 Channel：

![Channel 讨论收敛后的可继续输入状态](assets/kanban-channel-synthesis.png)

PRD Clarification 可以形成 canonical PRD/需求快照，但这仍是协作产物，不是执行 Task。
需要交付时，在 Kanban Agent 或 Channel 中明确：

```text
基于当前结论生成一个 Create Task proposal，不要自动启动 Workflow。
```

确认创建 Task 后，再进入 Task-bound Workflow Plan。PRD 拆分、planning artifact 和
`task_map` 属于所选 Workflow 的 planning 阶段，不由 Channel 或 Kanban Agent 提前伪造。

## 5. Channel 与 Research Workflow 的区别

`research-review` 是 Channel 模板，用于已有材料的多角色评审或轻量研究讨论。
它不会隐式点火固定 Research fanout。

真正的 Research Workflow 需要：

1. 一个真实 Task；
2. 用户明确要求 Research fanout；
3. 当前 Project 的 `zf workflow routes` 中存在且可用 `research:fixed`；
4. Kanban Plan 选择 route；
5. 独立 Approve exact proposal。

固定 Research 角色是
`source_researcher`、`product_analyst`、`technical_analyst`、`risk_critic` 和
`synthesizer`：

![Research Workflow 的固定角色与 request surface](assets/research-workflow-surface.png)

Research 输出为 summary、evidence refs、open questions、PRD/Refactor prompt inputs。
用户随后决定是否创建交付 Task；系统不自动把研究结果变成 PRD Workflow。

## 6. 低层 CLI：向已有 Channel 发消息

稳定 CLI 命令是 `zf channel say`：

```bash
zf channel say <channel_id> \
  --text "请补充失败场景，并由 @critic 复核。" \
  --member-id reviewer \
  --mention critic
```

| 参数 | 含义 | 默认 |
|---|---|---|
| `channel_id` | 目标 Channel | 必填 |
| `--text` | 消息正文 | 必填 |
| `--member-id` | 发言成员身份 | `agent` |
| `--mention` | @mention 成员，可重复 | 空 |
| `--state-dir` | 显式运行态目录 | 按 Project context 解析 |

该命令通过 `channel-post-message` ControlledAction 追加
`channel.message.posted`，不会直接写 `events.jsonl` 或持有飞书凭证。

`list`、`show`、`invite`、`synth` 尚不是稳定 Channel CLI 子命令。建群、邀请、权限、
讨论和 synthesis 由 Kanban Plan、Web API 或其他 ControlledAction 入口执行。

## 7. 飞书关联

飞书群可以投递到已有 Channel，也可以路由到 agent 直连会话：

```yaml
integrations:
  feishu_routing:
    oc_<chat_id>:
      target: channel
      channel_id: ch-login-security
```

使用 `target: agent` 时，Bridge 会为该 chat 建立对应 agent Channel 会话；使用
`target: channel` 时，消息进入指定多成员 Channel。入站 intent、按钮批准和出站投影
仍通过事件/ControlledAction 闭环，不能直接改 Task 或 Workflow canonical state。

完整配置见
[19 Feishu AI-Native 直连 Bridge](19-feishu-ai-native-direct-bridge.md)。

## 8. 成员与权限值域

ControlledAction 邀请成员时，常用 `member_type` 包括：

`provider_agent`、`runtime-role`、`human`、`observer`、
`readonly-reviewer`、`owner_delegate`。

常用 `channel_role` 包括：

`product_pm`、`arch`、`critic`、`synthesizer`、`researcher`、
`security_reviewer`、`dev_reviewer`、`qa_analyst`、`tech_leader`。

绑定 `zf.yaml` 已声明角色时使用 `runtime-role` 和
`workflow_role_binding: {"role": "<instance_id>"}`。`skill_refs` 按 Channel
成员的字面 skill path 物化，不复用 Workflow role 的 skill-pool 冲突消解。

权限、writer role 与 scope 必须由模板或 token-gated action 校验。即使宿主以
danger-full-access 启动，也不代表每个 Channel 成员自动获得 Project 写权限。

## 9. 观测与故障定位

```bash
zf events --last 100 | grep channel.
zf status --workers
```

重点检查：

- `channel.created` 与预期 template digest；
- required Members 是否全部 added/connected；
- 原始需求是否只有一次 `channel.message.posted`；
- discussion 是否进入预期 phase；
- synthesis artifact/ref 是否落地；
- 重试是否因 idempotency key 复用同一 Channel，而不是重复建群；
- provider 登录、预算和 writer scope 是否阻断成员回复。

## 相关

- [01 快速开始](01-quickstart.md)
- [20 Project 创建、Bootstrap 与 Workflow 点火](20-project-bootstrap-workflow-ignition.md)
- [19 Feishu AI-Native 直连 Bridge](19-feishu-ai-native-direct-bridge.md)
- [`zf.yaml` 控制面与运行态](02-zf-yaml-control-plane.md)
