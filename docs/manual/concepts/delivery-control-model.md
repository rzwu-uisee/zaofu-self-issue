# 从目标到可验证交付

[English](delivery-control-model.en.md) · [概念索引](README.md)

## ZaoFu 解决什么

Coding Agent 已经擅长在一个上下文内阅读和修改代码。ZaoFu 补的是团队级软件交付
控制：目标跨会话保持稳定，工作可分配，结果可验证，失败可恢复，人能看到状态并在
高风险动作前做决定。

ZaoFu 不替代 Provider Agent。Codex、Claude Code 或其他受支持 CLI 继续负责代码判断和
工具执行；ZaoFu 提供确定性运行边界、合同、接力、证据、恢复和操作面。

## 六个产品对象

| 对象 | 回答的问题 | 不负责什么 |
|---|---|---|
| Project | 哪个代码库、控制面和运行态属于这项工作 | 不自动创建 Task 或 Run |
| Channel | 需求和决策是否已被人和 Agent 聊清楚 | 不调度代码交付 |
| Task | 哪个有合同的工作单元由谁负责 | 不单独证明 Goal 已完成 |
| Workflow | Task 应经过哪些已批准阶段和门禁 | 不接受任意 Agent 生成的控制逻辑 |
| Run | 这次批准执行的 identity、attempt 和当前终态是什么 | 不由 Web projection 决定状态 |
| Delivery | Goal、Claim、Task、证据、Gap 和 Closure 是否一致 | 不创建第二套 truth |

## 一次界面接力

下图使用隔离的 `playgroud` Project，依次展示 Project admission、Project 状态、Task
合同、Delivery cockpit、Goal/Task Work graph 和 Recovery Loop。它们不是六套状态：
所有页面都读取同一个 Project/Run identity、canonical stores、事件账本及其查询投影。

![playgroud 从 Project 创建到 Delivery 与 Recovery Loop 的动态演示](../assets/concept-delivery-control-loop.webp)

## Goal 到 Closure

```text
Requirement
  -> Goal: 本次运行要达成的目标
  -> Goal Claims: 不可变、必须逐项解释的交付声明
  -> Task Map: 哪些 Task 覆盖 Claims、依赖和 owner
  -> TaskContract: 每个 attempt 的行为、范围、验证和非目标
  -> Result + Evidence: 实施和独立验证产物
  -> Gap: 未覆盖、失败、过期或身份不一致的事实
  -> Goal Closure: Claim 级结论
  -> Completion Gate: 机械一致性检查
  -> Goal Dossier + owner receipt
```

`Task done` 不等于 `Goal closed`。任务可能没有覆盖某个 mandatory Claim，验证结果可能
属于旧 generation，或者证据与目标 revision 不一致。

## 分层运行权威

| 层 | 载体 | 权威范围 |
|---|---|---|
| 控制平面 | `zf.yaml` | topology、role、policy、budget、safety 和 state dir |
| 发生账本 | `events.jsonl` | occurrence、ordering、causation、verdict 和 ref |
| 当前状态 | Task/Feature/Session/RoleSession/TaskAttempt stores | 当前操作状态和 lease |
| 完整语义 | artifacts、sidecars、accepted packages | plan、task map、result、evidence 和大 payload |
| 查询投影 | SQLite、Trace、Graph、Loop、Web summary | 搜索、聚合、可视化和 freshness |
| 活跃传输 | SSE、LiveDeltaBus、provider stream | 短时 delta，不参与恢复裁决 |

“事件可重放”不代表所有 canonical stores 都能只靠 `events.jsonl` 重建。删除 projection
可以重建，删除 required artifact 或 canonical store 可能破坏运行事实。

## Kernel 与 Agent 的边界

- Kernel/Orchestrator runtime：identity、准入、机械调度、schema、gate、replay、状态迁移和外部副作用。
- Agent/Skills：需求理解、计划、实现、评审、诊断和产品判断。
- Agent 通过 artifact、event 或 controlled-action proposal 报告事实和意图，不能直接写 canonical state。
- Web、CLI 和 Feishu 是读取与受控动作入口，不是状态机。

当前支持两种明确模式：

| 模式 | 快乐路径 owner | 适用范围 |
|---|---|---|
| Product Flow | Kernel 按已批准 topology/profile 机械派发；Layer 2 处理异常分诊和 proposal | PRD、Issue、Refactor、Research 和长期交付 |
| Legacy safe-team | 显式配置的 Layer 2 Agent 可做拆解、合同合成和 assign | 兼容、教学或显式手工编排 profile |

两者不能在文档或扩展中混写成同一个全局模型。

## 五类闭环

1. Delivery Loop：intake -> plan -> task map -> impl -> verify -> closure -> delivery。
2. Quality Loop：contract -> result -> evidence gate -> pass 或 negative handoff。
3. Recovery Loop：failure/stall -> Supervisor -> Run Manager -> controlled action -> post-verify。
4. Harness Improvement Loop：repeated fingerprint -> Autoresearch -> isolated proposal -> verify/apply。
5. Human Approval Loop：Plan hold -> approve/reject -> execute、repair 或停止。

这些闭环共享 identity、event 和 artifact ref，但不共享一个可变 Agent 状态机。

## 下一步

- [首个可验证交付](../getting-started/first-verified-delivery.md)
- [观察一次交付](../operations/observe-delivery.md)
- [恢复长期 Run](../operations/recover-long-running-run.md)
- [架构总览](../architecture.md)
