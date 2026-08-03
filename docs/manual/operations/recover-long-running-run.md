# 恢复长期 Run

[English](recover-long-running-run.en.md) · [运维索引](README.md)

> 恢复目标不是保证每个 Run 成功，而是让它继续产生有效进展，或在有界次数内带证据
> 收敛为 completed、blocked、failed 或 cancelled。

## 四个职责

| 组件 | 责任 | 禁止行为 |
|---|---|---|
| Supervisor | 观察、关联 failure/stall、提出 attention | 不直接 kill、retry 或改状态 |
| Run Manager | 从 current facts 选择至多一个有界 recovery action | 不并行执行互相冲突的旧 snapshot 动作 |
| ControlledActionService | 校验并应用批准的确定性动作 | 不接受无 currentness/权限的请求 |
| Autoresearch | 复现重复 fingerprint，产出诊断或隔离 repair proposal | 不直接 apply mainline 或伪造成功 |

Agent 可以报告 finding 和建议 replan，但不能直接写 continuation projection 或 Run
terminal。

## 先诊断，不先重试

```bash
zf status --workers
zf task trace TASK-ID
zf events --last 120
zf recover workflow --dry-run --json
zf projection doctor --projection all --json
```

这里的 dry-run 不追加 recovery event，也不改 Task/Run canonical state；它仍会写入可删除
重建的 workflow-resume projection，便于 Web 和后续 apply 回读。

先确认：

- 是 provider/transport 失败，还是语义验证失败；
- Task、Run、generation、attempt 和 dispatch identity 是否 current；
- pending handoff 是否真的缺失，而不是已经 settled/replayed；
- blocker 属于 setup、artifact、gate、lane、dependency、budget 还是产品语义；
- 是否已有更高优先级的 replan、repair 或 terminal decision；
- projection stale 是否仅影响显示。

## Continuation 模型

每个 active Run 的 `run-continuation.v1` 只选择零或一个 `next_operation`：

```text
events + stores + artifacts
  -> deterministic continuation reducer
  -> zero or one current next_operation
  -> ControlledAction
  -> outcome/progress evidence
  -> replay
```

Operation identity 绑定 run、generation、scope、checkpoint 和 failure fingerprint。相同
event replay 复用同一 identity；有效进展或 generation 改变后才产生新的 operation。

下面的 `playgroud` 动画展示同一个 Run 从 blocker、recovery planned、action applied 到
post-verify passed 的变化。恢复后 Delivery 继续进入 Verify，而不是把“恢复成功”误报为
“整个 Goal 已完成”。

![playgroud 长期 Run 的受控恢复与恢复后验证](../assets/recover-long-running-run.webp)

## 手工恢复 Pending Handoff

canonical 只读预览后，operator 可以对明确 checkpoint 做幂等恢复：

```bash
zf recover workflow \
  --resume-pending \
  --checkpoint-id CHECKPOINT-ID \
  --json
```

只有审核过新的 Task Map 时才提供 `--task-map-ref`。`--force-gate-dispatch` 是显式越过
常规 gate dispatcher 的 operator override，必须有独立理由和审计，不是普通重试按钮。

## 什么时候 Replan

以下情况不应继续盲重试原动作：

- 文件、接口、依赖或环境假设不成立；
- acceptance/evidence producer 不可满足；
- 多次相同 fingerprint 指向计划缺陷；
- scope、Task、AC、依赖或 topology 需要变化；
- stale generation 的 result/handoff 无法成为 current。

Agent/Planner 产出 semantic replan artifact/proposal，Kernel 校验 identity、currentness、
权限和准入后再 materialize。已完成 Task 不被静默改写；必要时创建 replacement/correction
Task 并保留 lineage。

## No-Progress 与终态

重复 resume 不等于进展。相同 scope/action 连续达到 no-progress cap，期间没有 Task、
fanout、verify、judge 或 delivery 正向里程碑时，系统应：

1. 写唯一 no-progress break；
2. 停止继续消费旧 recovery；
3. 写唯一 `run.goal.blocked`，带 fingerprint 和 evidence ids；
4. 不再为 terminal Run 选择 operation。

这比无限 active 和持续消耗预算更正确。新的有效 generation 或人工批准可以开始新的
受控 continuation，但不能抹掉旧 terminal 证据。

## 常见失败分类

| 分类 | 处理方向 |
|---|---|
| queued lane wait | 检查 scheduler/lane，不消耗 provider semantic attempt |
| transport/start failure | 修复 provider/session/transport 后幂等重投 |
| implementation failure | 返回当前 implementation owner，保留原 finding |
| verification failure | negative handoff 或 semantic replan，不伪装为 transport retry |
| candidate setup/gate | 在正确 candidate worktree 修复 setup 或合同 |
| artifact/currentness | 修复 ref/digest/generation/read，禁止采纳 stale result |
| repeated harness fingerprint | 交给 Autoresearch 诊断，不在同一 Run 无限修补 |

## 完成定义

恢复完成必须满足以下一种：

- Run 恢复后出现新的有效 progress milestone；
- 问题被 replan/replacement 接管，old attempt 明确 superseded；
- Run 带 blocker、evidence、owner 和 next action 收敛到 terminal；
- recurring harness 问题转成隔离、可验证的 Autoresearch 结果。

“worker 还活着”或“命令又执行了一次”都不是恢复完成。

## 相关

- [观察一次交付](observe-delivery.md)
- [故障排查](../07-troubleshooting.md)
- [Autoresearch](../10-autoresearch-usage.md)
