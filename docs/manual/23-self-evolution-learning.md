# 自我进化与能力积累

[English](23-self-evolution-learning.en.md) · [Autoresearch](10-autoresearch-usage.md) · [Metrics、Observability 与 Operations](21-metrics-observability-operations.md)

> 状态：`partial` / 显式启用。本文说明当前已经接线的、受证据约束的能力积累闭环；它不是模型训练，也不会默认自动修改源码、`zf.yaml`、Skills 或主分支。

## 1. ZaoFu 的自我进化是什么

ZaoFu 不会让 Agent 根据一次回答自行改写系统。它将已经验证的运行经验转换为可追溯、可评估、可撤销的能力资产，并只在适用的后续任务中复用。

```text
已验证的 Autoresearch Learn Run
  -> Run Archive 中的 typed capability deposition
  -> Run Manager 的 policy / 完整性检查
  -> immutable campaign + evolution attempt
  -> 隔离 baseline / candidate 重复 trial
  -> sealed evaluator 的独立比较
  -> learning asset candidate
  -> controlled transition / 独立 canary
  -> active_retained 或 revoked
  -> 按适用范围注入后续 Briefing
```

它解决的是“发现问题后，如何把有效修复变成可复用能力”：

- 把失败模式、操作手册、回归夹具沉淀为低风险学习资产；
- 对 Skill、Workflow、Provider route 等较高风险变更只形成证据绑定的 proposal；
- 用 baseline/candidate、重复 trial 与独立 canary 避免把一次偶然成功误判为能力提升；
- 记录使用结果和负迁移，必要时撤销已保留资产。

产品 Workflow 的 Task、Run、Gate 和 Delivery 仍由 Kernel 与 canonical stores 管理。Evolution 只能在产品 Run 终态后做附属学习工作，不能重新打开已完成的产品 Run 或改写其 Task 真相。

## 2. 闭环与职责边界

| 环节 | 当前所有者 | 负责什么 | 不负责什么 |
|---|---|---|---|
| Learn 输入 | Autoresearch Run Archive | 经验证的运行证据和一个 typed deposition | 直接采纳或改写能力 |
| Campaign / attempt | Run Manager + Evolution Coordinator | 冻结身份、policy、预算、trial 和生命周期 | 直接调用任意副作用 |
| Provider trial | Autoresearch resident | 在隔离环境执行 baseline、candidate、canary | 决定采纳结论 |
| 评估 | sealed evaluator authority | generation、隐藏 case、gate、分数与可比性 | 写入产品 Task 状态 |
| 采纳 | ControlledActionService / object-specific apply owner | 带 receipt 的状态转换或对象特定 apply | 绕过 owner approval |
| 复用 | CapabilityRegistry + briefing projection | 按适用范围选择资产并注入只读 Context | 让 Agent 直接写 canonical state |

自动 reconciliation 只消费可验证的 `autoresearch.loop.completed`、`mode=learn` 与 Run Archive，且 Archive 中必须恰有一个 typed deposition。普通 Event 文本、Web 投影或未归档对话不能直接变成学习资产。

## 3. 启用前提与最小配置

默认 `runtime.evolution.enabled` 为 `false`。第一次建议使用 `evaluate_only`：它可以建立证据、运行比较并输出 proposal，但不会自动把资产推进到保留状态。

```yaml
runtime:
  autoresearch_resident:
    enabled: true
    interval_seconds: 10
    max_actions_per_tick: 1
    worktree_root: /tmp/zaofu-autoresearch-resident/worktrees

  evolution:
    enabled: true
    mode: evaluate_only
    backend: codex
    trial_repetitions: 2
    trial_timeout_seconds: 300
    lease_seconds: 600
    max_trial_attempts: 2
    max_actions_per_tick: 4
    max_cost_usd: 2.0
    max_tokens: 50000
    sealed_root: .zf/evolution/sealed
    access_token_env: ZF_EVOLUTION_EVALUATOR_TOKEN
```

- `runtime.autoresearch_resident.enabled` 必须为 `true`，否则 evolution 配置 fail closed；
- `backend` 当前只接受 `codex` 或 `claude-code`；
- `sealed_root` 必填，且不得暴露给候选 Agent、Web 或普通 artifact 浏览器；
- `access_token_env` 是环境变量名称，不是 token 值；
- 只有 `mode: auto_low_risk` 可以无人干预推进显式 allowlist 中的 `memory_entry`、`runbook`、`regression_fixture`；
- `skill_prompt`、`framework_code`、`workflow_config`、`provider_route`、`tool_capability`
  仍为 proposal-only；Skill Candidate 只能先成为 scoped overlay，写入 canonical source 仍须
  owner action token 和对象特定 apply 路径。
- `skill_prompt` 不在默认 `auto_asset_kinds` 中。只有显式加入 allowlist，才能自动推进到
  scoped canary；无论是否在 allowlist，已激活 overlay 的 source drift、过期、预算越界或
  negative transfer 都可由 Run Manager 经 Controlled Action 安全撤销。

先验证配置，不启动真实 Provider：

```bash
uv run zf validate --cold-start
```

请先确认项目的 `project.state_dir`。以下命令使用 `$STATE_DIR` 表示该目录，不能假定所有项目都使用 `.zf`。

## 4. 如何观察一次 campaign

启动已启用的 runtime 后，先读取只读投影和事件流：

```bash
STATE_DIR=/path/to/configured-state-dir

uv run zf evolution status --state-dir "$STATE_DIR"
uv run zf watch --type evolution.campaign.materialized --follow --state-dir "$STATE_DIR"
uv run zf watch --type evolution.trial.execution.completed --follow --state-dir "$STATE_DIR"
uv run zf watch --type evolution.canary.completed --follow --state-dir "$STATE_DIR"
```

按以下顺序判断 campaign 是否可信：

1. `campaign.materialized` 是否绑定来源 Run Archive、deposition digest 和 policy digest；
2. baseline 与 candidate 是否在同一 attempt 下都完成了重复 trial；
3. evaluator generation、environment fingerprint、TCB 和 Archive 是否可验证；
4. comparison 是否可比较，而非 `incomparable`、超时或基础设施失败；
5. 是否经过独立 canary，最终资产状态是 `active_retained` 还是 `revoked`；
6. 资产在真实后续任务中使用后，是否记录了 usage outcome 与负迁移。

关键证据通常位于配置的 state dir：

```text
events.jsonl                         发生顺序、因果和受控动作事实
evolution/trials.json                attempt 与 trial lease/settlement 当前状态
evolution/capabilities.json          learning asset 生命周期与当前版本
evolution/attempts/                  immutable evolution-attempt sidecar
evolution/campaigns/                 immutable campaign sidecar
evolution/snapshots/                 环境和 policy 冻结快照
runs/                                trial、canary 的可验证 Run Archive
```

`zf evolution status`、Web 和 Graph 都是读取面。判断采纳结论时，应回到 EventLog、Registry 和 immutable sidecar，而不是根据卡片颜色或单个分数。

## 5. 评估、环境与 canary 门禁

一次能力提升至少应满足：

- Gate 先于分数。required gate 或 score dimension 缺失、类型/范围错误、阻断 gate 失败时，不能由总分抵消；
- baseline/candidate 使用冻结 evaluator generation 和可比较输入；环境不等价时结论是 `incomparable`；
- sealed evaluator 正文不进入候选 Context；canary 使用不同 generation；
- trial 有 lease、idempotency key、最大尝试次数和 single-winner settlement，重启不应重复计费或重复采纳；
- 真实 Provider 前执行 environment preflight，核对 Provider、CLI/toolchain、锁文件、sandbox/network/credential capability 快照；
- 基础设施失败通过有界 retry/dead-letter 收敛，不是“candidate 比 baseline 差”的语义结论。

环境快照只证明执行依赖可比较，不能替代业务验收。Delivery、Task Gate 和终态 evidence 仍遵守原有 Workflow 合同。

### 5.1 Skill 的评测、优化与停用

Skill 使用 `raw/current/candidate` 三臂，而不是 generic asset 的 baseline/candidate 两臂。
正式采纳要求冻结同一 model、workspace、prompt、支撑 Skill、evaluator generation 和 routing
pool，并至少完成 policy 要求的 distinct cases 与 replicates。Candidate 必须通过正常 Codex
或 Claude Code Skill 投影加载；把 Candidate 正文直接拼入 prompt 不算 Skill 评测。

```text
immutable Skill Candidate
  -> 三臂 counterbalanced Provider trials
  -> Run Archive + typed routing/feedback evidence
  -> proposal_only learning asset
  -> canary_active scoped overlay
  -> negative outcome: automatic revoke for future dispatches
  -> passed canary: exact source proposal
  -> owner token apply + provider parity sync
  -> active_retained
```

常用只读/受控入口：

```bash
uv run zf evolution skill-overlay-resolve \
  --state-dir "$STATE_DIR" --role impl --task-family prd --cohort canary-a

uv run zf evolution skill-source-propose \
  --state-dir "$STATE_DIR" --asset-id <asset-id> --version <version>

ZF_EVOLUTION_OWNER_TOKEN='<owner-token>' \
uv run zf evolution skill-source-apply \
  --state-dir "$STATE_DIR" \
  --proposal-ref-file /path/to/proposal-ref.json \
  --owner-token-file /path/to/supplied-token

uv run zf evolution skill-maintenance-propose \
  --state-dir "$STATE_DIR" --skill <skill-name> \
  --action deactivate --evidence-refs-file /path/to/evidence-refs.json \
  --rationale 'matched outcomes show sustained negative transfer'
```

自进化可以自动撤销 `canary_active` overlay，使后续 dispatch 不再加载 Candidate；role、
task family 与 cohort 均须匹配，当前 dispatch 的 cohort 使用精确 Task ID。它不会
修改已经启动的 Provider context。Autoresearch 可生成 `optimize/replace/merge/deactivate`
proposal，但 canonical `skills/` 的优化、替换、禁用或删除必须由 owner 批准、校验精确
source currentness，并同步 `.codex/skills/` 与 `.claude/skills/`。当前没有“Agent 自行卸载
源码 Skill”的合法路径。

### 5.2 纯因果 Skill 优化器

正式 Skill 优化使用 `skill-treatment-identity.v2` 冻结 runtime commit、Provider、model、
role/profile、prompt、支撑 Skill、workspace fixture、tool、sandbox、network、budget 和 evaluator
generation。Raw、Current、Candidate 三臂只允许目标 Skill 的可用性、版本和 digest 不同；公共
身份漂移时，本轮比较是 `incomparable`，不能形成提升结论。

Provider case 同时产出两类相互独立的证据：

```text
最终输出 -> correctness / product gate
Provider stream -> immutable normalized trajectory -> behavior verdict
```

因此，输出正确但未读取目标 Skill 时，correctness 可以通过而 behavior 仍为 `false`；没有显式
可观察行为合同则为 `null`。轨迹正文保存在 sidecar，EventLog 只保存 ref、digest 与 verdict。

Optimizer campaign 使用互斥的 Train、Selection、Test：

- Optimizer Agent 只读取 current Skill、Train evidence、failure clusters 和 rejection buffer；
- Selection evaluator 只用于逐步选择，并必须绑定精确 split、generation 和 case result refs；
- Test 保持 sealed，只用于最终 179 adoption proof；best candidate 不会直接写入 `skills/`。

生产闭环由 Autoresearch resident 执行 proposal-only Agent request，sealed evaluator 发布
Selection，Run Manager 校验 currentness 并结算。常用恢复/运维入口：

```bash
uv run zf evolution skill-opt-agent-execute \
  --state-dir "$STATE_DIR" \
  --request-event-id <proposal-request-event-id>

uv run zf evolution skill-opt-selection-submit \
  --state-dir "$STATE_DIR" \
  --selection-request-event-id <selection-request-event-id> \
  --evaluation-file /path/to/sealed-selection-result.json
```

`skill-opt-init`、`skill-opt-prepare`、`skill-opt-settle` 和 `skill-opt-export` 仍是机械调试与
恢复入口。只有带三个 immutable split descriptor 的 v2 campaign 能进入 Agent route；旧 v1
campaign 仅用于历史恢复。完成的 best 仍进入 Design 179 的 test、routing、canary 和 owner retain
流程。

## 6. 记忆：工作笔记与学习资产

当前有两类跨 session 信息，不应混为一谈：

| 类型 | 保存位置 | 用途 | 当前边界 |
|---|---|---|---|
| 普通 Memory | `memory/shared.md`、`memory/<role>.md` 及 archive | 跨 session 的决策、修复和 Context 笔记 | `max_days` 目前是元数据，默认读取不是严格的 per-entry expiry/applicability gate |
| Retained Learning Asset | `evolution/capabilities.json` + immutable artifact | 经评估/canary 后的可复用经验 | 仅在状态、适用范围、过期、冲突和 canary scope 均满足时注入 |

普通 `memory.note` 适合运行接力，但不能单独证明某条经验提升了后续工作。自我进化资产要求关联 evidence、版本、适用范围、使用和 outcome，才会进入长期复用层。

任务或 Orchestrator Briefing 会按以下条件筛选 Retained Learning：

- 生命周期必须是 `canary_active` 或 `active_retained`；
- 不得过期、带 contradiction ref，或被标记为 secret/PII/license_unknown 等污染；
- task family、Provider、model、语言、仓库和 canary scope 必须匹配；
- 不匹配资产保留 excluded reason，不应静默注入。

`memory_entry` 与 `runbook` 的正文可作为只读 prompt Context；其它资产只能按其受控对象路径应用，不能把一段自由文本直接当作控制面。

## 7. Context：当前注入与严格重放

每次 Worker 或 Orchestrator dispatch 都会动态组装 Context：

```text
Task contract / Task Capsule
近期事件、进度和恢复信息
仓库指导与已加载 Skill
普通 Memory
适用的 Retained Learning
运行规则、Provider/session 与受控工具边界
```

Evolution attempt 合同已预留 `briefing`、`context read set`、`skill lock`、`memory snapshot`、`tool policy` 和环境快照的 ref/digest。

但当前自动 campaign materialization 对 `context read set`、`skill lock`、`memory snapshot` 仍复用来源 deposition 的 ref/digest，尚未为每个 trial 固化“实际读取的精确 Context、Skill 版本和 Memory 条目”。正确口径是：

- 可以追溯学习候选来自哪个已验证 Run Archive，并验证 evaluator 与环境；
- 可以安全筛选并注入已保留资产；
- **不能**把历史 campaign 宣称为完整 prompt/context 的逐字可重放记录；
- environment capability snapshot 已独立冻结并 preflight，但不等同于 prompt/context snapshot。

需要严格复现 Skill 或 Memory 实验时，应将 briefing、Skill digest 和 Memory 读取证据作为额外 artifact 保存；不要只依赖 campaign 页面。

## 8. 生命周期、回退与人工边界

```text
candidate -> validated -> approved -> canary_active -> active_retained
                  任一阶段可因证据不足、负迁移、冲突或 policy 变化 -> revoked
```

Evolution Coordinator 不会自主编辑源码、`zf.yaml`、`skills/` 或普通 Memory。Skill retain
只能通过显式 `skill-source-apply` owner action 写入 canonical source；对象特定 apply owner
必须提供 immutable receipt，状态转换使用 revision/CAS 保护。

回退：

1. 在新 runtime generation 中将 `runtime.evolution.enabled` 设为 `false`，停止新 campaign；
2. 将 `mode` 改为 `evaluate_only`，只停止自动采纳；
3. 用带 receipt 的 controlled transition 将已激活资产改为 `revoked`，不要手改 `capabilities.json`；
4. 保留 EventLog、trial、comparison、canary 和 receipt 以便审计；
5. 不删除产品 Task、Delivery、Run Archive 或历史 artifact 来“清理失败”。

`zf evolution asset-transition` 与 `asset-outcome` 是给 controlled action 和运维工具的机械接口，不是绕过 owner gate 的手工快捷方式。

## 9. 推荐启用顺序

1. 先用 `evaluate_only` 和一个可复现的 `runbook` 或 `regression_fixture` 验证完整 evidence 链；
2. 检查成本、timeout、environment preflight、sealed evaluator 和 canary；
3. 只在证据稳定后启用 `auto_low_risk`，保持最小 `auto_asset_kinds` allowlist；
4. 定期审查 retained asset 的 usage outcome、过期、冲突和 negative transfer；
5. 对 code、Workflow、Provider route、工具能力变更继续使用 proposal -> owner approval -> controlled apply。

相关路径：

- [Autoresearch](10-autoresearch-usage.md)：产生受验证诊断、repair 和 Learn 输入；
- [Supervisor Inspection](12-supervisor-inspection-usage.md)：观察 attention 和恢复候选；
- [Metrics、Observability 与 Operations](21-metrics-observability-operations.md)：区分 Event、Log、Metric 与 Delivery 事实；
- [上下文、Artifact 与 Handoff](operations/context-handoff-artifacts.md)：了解 required reads、lineage 和恢复接力。
