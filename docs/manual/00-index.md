# ZaoFu 使用手册

[English](00-index.en.md)

> 面向 ZaoFu 使用者和操作者。本文按“你要完成什么”路由，不要求先阅读架构或
> CLI 全量目录。实现边界以当前代码、测试和本手册的可执行检查为准。

## 选择你的路径

| 你现在要做什么 | 从这里开始 | 完成标志 |
|---|---|---|
| 第一次安装并完成一次可验证交付 | [首个可验证交付](getting-started/first-verified-delivery.md) | Task 进入 Workflow，Delivery 能解释结果和证据 |
| 用多人和 Agent 把模糊需求聊成 PRD | [Channel 到 PRD](workflows/channel-to-prd.md) | Owner 确认 canonical PRD，并得到来源回执 |
| 为已有 Task 选择并启动 Workflow | [受控 Workflow 启动](workflows/controlled-workflow-start.md) | exact proposal 获批并产生 `workflow.invoke.requested` |
| 判断一个长期目标是否真的完成 | [观察一次交付](operations/observe-delivery.md) | 能从 Goal、Claim、Task、Evidence、Gap、Closure 解释结论 |
| 处理停滞、失败或无进展 Run | [恢复长期 Run](operations/recover-long-running-run.md) | Run 恢复推进，或有证据地收敛为 blocked/failed/cancelled |
| 理解跨 Agent 的上下文和证据如何继承 | [上下文、Artifact 与 Handoff](operations/context-handoff-artifacts.md) | 能定位 current contract、required reads、result 和 lineage |
| 接入飞书、Automation 或 Provider | [集成](integrations/README.md) | 外部入口通过 projection/controlled action 工作，不产生第二真相 |
| 查命令、配置或稳定合同 | [参考](reference/README.md) | 从生成的当前命令目录或专题参考定位准确入口 |
| 开发、评审或验证 ZaoFu 本身 | [架构总览](architecture.md) | 能区分 Kernel、Agent、Store、Artifact、Projection 和两种编排模式 |

## 一个产品心智

```text
Requirement
  -> confirmed Goal and Claims
  -> approved Workflow and Task Map
  -> contracted Agent attempts
  -> independent verification and evidence
  -> Goal Closure and owner-visible delivery
  -> bounded recovery or explicit terminal blocker
```

ZaoFu 的一级产品对象是 Project、Channel、Task、Workflow、Run 和 Delivery。Graph、
Trace、Loop、SQLite、Web summary 是这些对象的查询和表达方式，不是第二套调度状态机。

## 按主题浏览

- [入门](getting-started/README.md): 最短成功路径和完整安装路线。
- [概念](concepts/README.md): 交付模型、运行权威和 Loop Engineering。
- [工作流](workflows/README.md): Channel、PRD、Issue、Refactor、Research 与受控点火。
- [运维](operations/README.md): 观察、批准、恢复、上下文接力和 Web 维护验证。
- [集成](integrations/README.md): 飞书、Automation、Provider 和外部入口。
- [参考](reference/README.md): parser 生成的 CLI 目录、配置与 currentness 规则。
- [案例](showcases/README.md): 可复现演示和证据边界。

## 兼容专题

以下高流量路径继续保留；新读者优先使用上面的任务路径。

- [01 完整快速开始](01-quickstart.md)
- [02 `zf.yaml` 控制面](02-zf-yaml-control-plane.md)
- [03 CLI 操作流程](03-cli-operations.md)
- [04 Harness 运行流程](04-harness-runtime.md)
- [05 Skills、Workdir 与 Git Evidence](05-skills-workdirs-git-evidence.md)
- [06 Web Dashboard 使用](06-web-observability-e2e.md)
- [07 故障排查](07-troubleshooting.md)
- [08 创建 Task、Assignment Intent 与 Agent 协作](08-new-task-agent-squad.md)
- [09 CLI 使用参考](09-zaofu-cli-usage.md)
- [10 Autoresearch](10-autoresearch-usage.md)
- [11 Feishu Automation、Kanban 与 Project 协作群](11-feishu-automation-kanban-sync.md)
- [12 Supervisor Inspection](12-supervisor-inspection-usage.md)
- [13 Plan、Task Map 与调度](13-plan-task-map-orchestrator-dispatch.md)
- [14 Delivery Trace](14-delivery-trace-usage.md)
- [15 Channel 协作](15-channel-collaboration.md)
- [16 真实 Provider Preflight](16-real-codex-provider-preflight.md)
- [18 Product Fanout E2E](18-product-fanout-real-e2e.md)
- [19 Feishu AI-Native Bridge、实时会话与审批](19-feishu-ai-native-direct-bridge.md)
- [20 Project、Bootstrap 与 Workflow 点火](20-project-bootstrap-workflow-ignition.md)

## 文档状态

能力是否存在不能仅由设计标题推断。每个当前能力必须同时有用户手册、代码入口和
测试证据，并登记在
[能力覆盖清单](reference/capability-coverage.md)。文档维护规则见
[文档 currentness 与发布门禁](reference/documentation-currentness.md)。
