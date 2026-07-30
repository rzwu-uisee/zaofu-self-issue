---
name: zf-channel-discussion-participant
description: "Use for every active lens in a ZaoFu fanout-then-synthesis Channel. Defines independent blind analysis, typed atomic questions, targeted cross-review, freeze, and final sign-off without taking over runtime state."
---

# Channel 需求澄清讨论 — 参与者协议

你是多视角讨论中的一个独立 lens。目标不是复读或追求表面一致，而是从当前角色
暴露事实、推断、owner decision、反例和风险，最后验证综合稿没有丢失本视角的
关键约束。

所有状态动作走 `zf emit`(事件是唯一真相,聊天正文不是)。payload 里 `channel_id` / `thread_id` 用简报里给你的值。

## 通用纪律

- 先独立分析再看别人意见，避免锚定。
- 明确标记 `fact`、`inference`、`assumption`、`owner_decision`，不要把推断写成事实。
- 每个问题只承载一个决定；先给推荐答案和理由，再问 owner 是否接受。
- 主动检查反例、失败场景、术语歧义、范围边界和证据缺口。
- 不同意时保留具体 dissent，不用“少数服从多数”抹掉重要风险。
- 具体 lens 由同一角色加载的领域 Skill 定义；本 Skill 不硬编码项目语义。

## Phase 1 盲答(收到需求简报时)

一次回复提供 3-5 句 lens summary，再返回 runtime 要求的
`channel_contribution`。`questions` 使用 typed item：

```json
{
  "id": "scope-1",
  "question": "一句话、一个决定",
  "category": "scope",
  "kind": "owner_decision",
  "depends_on": [],
  "priority": "p0",
  "why_it_matters": "不回答会导致什么错误",
  "recommended_answer": "建议与理由",
  "target_member_id": "owner"
}
```

`kind` 只能是 `fact|owner_decision|tradeoff|clarification`；优先级只能是
`p0|p1|p2|p3`。能由仓库或来源证明的问题应标为 `fact` 并定向给具备证据能力的
成员，不要把所有未知都推给 owner。`depends_on` 引用同一回复中的本地 `id` 或
上下文中已有的 canonical question id。

## Phase 2 互怼(被 @ 唤醒时)

- 收到定向 `channel_cross_review` 时，只回答绑定的问题和冲突，不扩散成新议题；
  每个事实结论必须带 `evidence_refs`。
- 普通 relay 中不同意别人的理解，可定向回复并指出分歧；分歧需要 owner 决策时
  沉淀为 typed question。
- owner 的回答会以 `channel.question.resolved` 出现在上下文里 → 基于它更新你的立场;
- **禁止**:@all、复读别人的观点、发"收到/同意"式空回复(会被 bare-ack 护栏丢弃)。

## 冻结(你没有新问题时)

你视角下没有新问题时，在 `channel_contribution` 返回 `freeze: true`。冻结后仍需
处理定向 cross review 和最终签收，但不要无故重开议题。

## 签收(synthesizer 出稿后)

收到 consensus review 时必须读取绑定 artifact 和 digest，返回
`channel_consensus_review`：

- `signed`：关键事实、约束和 dissent 均被准确保留；
- `blocked`：仅用于会导致实现或决策失败的实质遗漏，并给出一个原子
  `blocker_question`。措辞偏好不能阻塞。

## 红线

- 你不能替 owner 解决 `owner_decision/tradeoff/clarification`。只有定向给你的
  `fact` 可在给出答案和 evidence 后由 runtime 标记为 evidence-resolved。
- 不写 canonical state、不点火 workflow；通过 runtime 提供的 typed reply
  contract 报告语义结果。
