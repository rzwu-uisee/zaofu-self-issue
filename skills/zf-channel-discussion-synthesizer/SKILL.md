---
name: zf-channel-discussion-synthesizer
description: "Use when you are the synthesizer (usually product_pm) of a ZaoFu channel requirement-clarification discussion (doc 122). Defines question dedup, the clarified-requirement artifact format, and the consensus proposal flow."
---

# Channel 需求澄清讨论 — Synthesizer 协议

你除了自己的视角发言(见 participant 协议),还负责两件只有你做的事:**合并重复问题**和**收敛成稿**。

## Phase 2 开场:台账去重

盲答后三个视角的问题必有重叠。逐对合并(保留问得更锋利的那个):

```bash
zf emit channel.question.merged --actor <你的member_id> --payload '{"channel_id":"<CH>","thread_id":"main","question_id":"<被合并的q>","into_question_id":"<保留的q>"}'
```

## 收到 synthesis 请求时(channel.synthesis.requested 指向你)

台账已清零。产出**澄清需求 artifact**:

1. 在结构化回复中提供完整 PRD 字段。Runtime 会原子写入
   `.zf/channel-artifacts/<channel>/<synthesis-request>.md` 并绑定 sha256:

```markdown
# 澄清需求:<标题>
## Decisions(逐条:问题 → owner 的回答)
## Assumptions(显式假设 + 风险)
## Out of Scope(明确不做)
## Acceptance Criteria(EARS 句式:When <触发>, the <系统> shall <行为>)
```

只写台账里有据的内容——**每条 Decision 必须能对应一个 resolved question**,不发明 owner 没说过的决定。

2. 在 channel 回复末尾输出 `channel_synthesis` JSON，包含 `title`、
   `decision`、`summary`、`decisions`、`assumptions`、`out_of_scope`、
   `acceptance_criteria`、`open_questions`、`risks`、
   `recommended_workflow`、`source_refs` 和 `confidence`。Runtime 负责
   `channel.synthesis.proposed`、artifact 写入和唯一 consensus proposal；
   不要自行写 canonical event 或 state。

3. 出现 `blocked` → 讨论自动重开，blocker 进台账 → owner 答完后重新综合。

Runtime 会把你的结构化综合绑定为 proposer 签名。Owner 确认后 kernel 只关闭
讨论并发布 canonical PRD；Task 创建和 Workflow 点火必须由 owner 在 Channel 或
Kanban Agent 上另行发起，不自动执行。
