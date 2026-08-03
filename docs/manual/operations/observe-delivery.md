# 观察一次交付

[English](observe-delivery.en.md) · [运维索引](README.md)

> 目标：不阅读全部 provider transcript，也能回答“目标是什么、现在到哪、为什么没完成、
> 证据是什么、谁继续处理”。这些页面是只读 projection，不拥有调度权。这里的“只读”
> 指不改 canonical Task/Run/Event；查询可以增量刷新可删除重建的 SQLite/JSON projection。

## 先选正确对象

| 你要回答的问题 | 先打开 |
|---|---|
| 当前有哪些工作，谁负责 | `Tasks` |
| 一次目标整体是否可交付 | `Delivery` |
| 某轮 stage/attempt/retry 如何推进 | `Delivery -> Runs` |
| Goal 的每个 Claim 是否被覆盖 | `Delivery -> Delivery Map -> Coverage` |
| Goal、Claim 和 canonical Task 如何关联 | `Delivery -> Delivery Map -> Work` |
| runtime/gate/artifact 技术诊断 | `Delivery -> Delivery Map -> Diagnostics` |
| 事件因果和精确发生顺序 | `Monitoring -> Observability -> Traces/Events` |
| 一个 Run 的人类可读过程包 | `Monitoring -> Observability -> Runs -> Goal Dossier` |
| 等待人工决定或需要关注什么 | `Inbox` |

下面的 `playgroud` 动画按一次交付的排查顺序，依次展示 Overview、Runs、Coverage、Work、
Diagnostics、Loop 和 Observability；所有页面读取同一 Feature/Run/Event 链。

![playgroud 交付观测：Overview、Runs、Graph、Loop 与 Observability](../assets/observe-delivery.webp)

## Delivery Overview

Overview 先回答：

- 当前 verdict 和 ship readiness；
- 当前 phase/cycle；
- blocker、why-not-done 和 next owner；
- 总 Task、完成比例、成本和持续时间；
- 是否存在 drift、rework 或 recovery。

它是进入深层视图的摘要，不应重判 Task、Run 或 Closure。

## Runs 与 Spans

`Runs` 以本轮执行为单位展示：

- stage 和 role；
- attempt、dispatch identity 和 retry；
- queued/assigned/running/terminal 生命周期；
- fanout/fanin 和 dependency barrier；
- gate、result、duration 和 causation。

`Spans` 用于定位事件顺序和调用因果。运行慢或看似跳步时先看 Runs，再用 Spans/Trace
定位具体 event，不要从一个状态 badge 猜原因。

## Coverage、Work、Diagnostics

三种 Graph 视角回答不同问题：

| 视角 | 主要受众 | 解释什么 |
|---|---|---|
| Coverage | PM、Owner、Reviewer | Claim 的 Plan、Implementation、Verification、Closure 和 Gap |
| Work | 工程师、交付 Owner | Goal -> Claim -> Task，及 Task 的 Try、Result、Evidence |
| Diagnostics | Operator、Harness 维护者 | runtime、gate、behavior、eval 和 artifact 技术关系 |

Coverage 中 `Task done` 但 Claim 未闭合通常表示：

- Task 没有声明覆盖该 Claim；
- verification 结果缺失、失败或属于旧 generation；
- evidence 与 target/contract identity 不一致；
- Goal Closure 仍有 open gap。

Work 中同一个 canonical Task 只应有一个主节点；它覆盖其他 Claim 时显示 secondary
relation，避免多个节点形成多份状态真相。

## Goal Dossier

Goal Dossier 是 run-scoped、可删除重建的人类可读投影，包含：

- Goal objective 和 terminal status；
- roadmap/Tasks；
- mandatory Claim coverage；
- evidence index；
- active/resolved gaps；
- closure、delivery readiness 和 source freshness；
- source fingerprint 和 refs。

它负责解释一个 Run，不负责决定一个 Run。Dossier 与 terminal/receipt 不一致时，系统
可以抑制错误的 owner 完成消息，但不能从 Dossier 反向改 canonical terminal。

CLI 生成同一投影：

```bash
zf report goal-dossier --run-id RUN-ID --out /tmp/goal-dossier
```

该命令写指定输出目录，也会刷新 state dir 下可重建的 Goal Dossier/SQLite projection；
不会反向修改 canonical terminal。

## Inbox

Inbox 聚合需要人处理的事项：

- Plan/Workflow approval；
- human decision 或 waiver；
- runtime attention 和 recovery decision；
- run delivery/owner receipt；
- integration 或 automation notification。

处理 Inbox action 会走 token-gated controlled action，并产生审计事实。标记已读不改变
Task/Run 业务状态。

## CLI 对账

```bash
zf kanban --board
zf task trace TASK-ID
zf trace delivery FEATURE-ID
zf events --last 100
zf projection status --json
zf projection doctor --projection all --json
```

Projection 显示 stale/degraded 时先诊断 projection；不要因为页面空白就修改 canonical
store。`status`/`doctor` 为诊断可能初始化 SQLite schema 或刷新 projection 元数据；只有
显式 `repair`/`rebuild` 才可 quarantine 或完整重建对应读模型。

## 签收问题

关闭一次交付前能回答：

1. 原始 Goal 和 mandatory Claims 是什么？
2. 哪些 Task 覆盖每个 Claim？
3. 当前 result/evidence 属于哪个 run、generation、contract 和 target？
4. 哪些 Gap 仍未闭合，owner 和 next action 是谁？
5. Completion Gate 为什么通过或阻塞？
6. Owner 收到的结论是否与 Goal Dossier 一致？

回答不了时，状态仍不具备可解释交付条件。

## 相关

- [从目标到可验证交付](../concepts/delivery-control-model.md)
- [Delivery Trace 深度参考](../14-delivery-trace-usage.md)
- [恢复长期 Run](recover-long-running-run.md)
