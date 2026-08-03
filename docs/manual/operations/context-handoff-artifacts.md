# 上下文、Artifact 与 Handoff

[English](context-handoff-artifacts.en.md) · [运维索引](README.md)

> 目标：Agent、session、provider 或 stage 变化时，不靠聊天记忆猜当前目标、合同、代码
> revision 和证据。完整语义通过 immutable artifact/sidecar 保存，查询和 briefing 只交付
> 当前 attempt 所需切片。

## 三类内容不要混淆

| 内容 | 当前载体 | 作用 |
|---|---|---|
| Current identity/state | Task/Session/TaskAttempt stores、EventLog verdict/ref | 决定哪个 run/generation/attempt/contract current |
| Complete semantic body | artifact、sidecar、accepted package | 保存 plan、Task Map、result、evidence、diagnostics、conversation body |
| Query/context projection | SQLite catalog、StatePacket、Goal Dossier、briefing section | 查找、聚合和有界交付，不成为 authority |

Event preview、文件名、Web badge 或 transcript 片段不能替代 required artifact body 和
digest 校验。

Task 的 Summary、Activity、Evidence、Advanced 与 Agent 资源视图提供同一交付上下文的
不同只读切片；切换页面不会改变 canonical state：

![Task 上下文、Artifact、Handoff 与 Agent 资源动态演示](../assets/task-context-handoff.webp)

## Attempt Source Manifest

每个 provider attempt 应收到显式输入清单：

- current TaskContract 和 revision；
- target/source revision；
- Task Map generation 和 Plan Package；
- stage required reads；
- previous admitted result、negative feedback 或 recovery delta；
- artifact identity、locator、digest、access scope 和 retention；
- actor/role/provider/purpose。

读取正文前必须授权 exact occurrence；知道一个 ref 存在不代表当前 Actor 可以 hydrate。

## 查询当前上下文

Agent-facing Task capsule：

```bash
zf ctx --task TASK-ID --mode implement --json
zf ctx --task TASK-ID --mode check --json
```

Attempt 输入和 read 状态：

```bash
zf attempt inspect ATTEMPT-ID
zf artifact list --attempt ATTEMPT-ID --json
zf attempt missing-reads ATTEMPT-ID
```

读取授权输入的有界正文：

```bash
zf artifact read \
  --attempt ATTEMPT-ID \
  --source SOURCE-ID \
  --artifact ARTIFACT-ID \
  --max-chars 12000
```

该命令只能在已派遣 attempt 的 Worker 上下文中执行，runtime 会注入 attempt-scoped
credential；普通 operator shell 会 fail closed，并追加拒绝审计事件。成功读取会追加 read
evidence。缺 required read 时应先补读或执行同 attempt protocol repair，
不能把“Agent 没读输入”误路由成产品语义 rework。

## Artifact Catalog 与 Lineage

SQLite catalog 加速 object/occurrence/lineage 查询，但不是 current selector：

```bash
zf artifact catalog list --help
zf artifact catalog show --help
zf artifact catalog lineage --help
zf projection status --json
zf projection doctor --projection artifact-catalog --json
```

同一 content digest 可以有多个 occurrence。权限、Task、stage 和 event context 属于
occurrence，不应错误 union 到 content object。Catalog stale/corrupt 时，dispatch-critical
路径仍需 canonical resolver 或 fail closed。

## Handoff 与 Session Continuity

人类可读或 Agent resume packet：

```bash
zf handoff --format md
zf handoff --format state-packet --task TASK-ID --score
```

可靠 handoff 至少包含：

- current Task、Run、attempt、dispatch 和 owner；
- objective、contract、scope、non-goals 和 acceptance；
- target revision、worktree/branch evidence；
- completed work 和 admitted results；
- unresolved feedback、required reads 和 next action；
- refs/digests，而不是复制全部 transcript。

Provider session resume 保留对话连续性，但不拥有 Task truth。Session 被回收、换 Provider
或换 Agent 后，新的 briefing/context envelope 仍必须从 canonical facts 重建，而不是把
旧对话当作 current contract。

## Freshness 与 Currentness

检查一个结果能否接力时，至少核对：

1. run/Task/attempt/operation identity；
2. Task Map generation 和 contract revision；
3. target/source commit；
4. artifact digest/schema；
5. admitted result event/ref；
6. required-read ledger；
7. superseded/terminal 状态。

任何一项不一致时应显示 stale diagnostic、修复引用或创建新的 attempt，不得让迟到结果
覆盖 current 结果。

## 数据与权限边界

- 大 stdout/stderr、provider output、conversation body 和 context package 放 sidecar，不塞入事件预览。
- Artifact read 受 PathGuard、access scope、actor/purpose 和 retention 约束。
- SQLite 可删除重建，不能由 Agent 写入并影响调度。
- Required artifact hydration 失败应 fail closed；可选 UI sidecar 可 degraded 展示。
- 跨 Project 不自动传播正文或 memory。

## 完成定义

一次跨 Agent/Session Handoff 完成需要：接收方能在不读取旧完整 transcript 的情况下，
准确得到 current Goal/Task/Contract/Target、必须读取的输入、已接纳结果、未闭合反馈和下一
动作，并留下 required-read 证据。

## 相关

- [观察一次交付](observe-delivery.md)
- [Skills、Workdir 与 Git Evidence](../05-skills-workdirs-git-evidence.md)
