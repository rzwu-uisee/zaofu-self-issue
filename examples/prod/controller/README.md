# Production Controller Examples

这些示例是 operator 面向生产 run 的短 YAML 入口。它们展示 typed
controller + profile composition,不是 runtime 执行时的第二控制面。
这是新的推荐产品入口;`examples/prod/new/*.yaml` 只作为 expanded LKG/E2E
回归样本保留,不推荐 operator 手写复制。

## 入口选择

| 入口 | 适用场景 | YAML | 组合来源 |
|---|---|---|---|
| PRD build(fanout) | 从 PRD / idea 构建新功能或产品(多 lane) | `prd-fanout-v3.yaml` | `prod-runtime/v1` + `PrdFlow` 展开(roles/stages/pipeline 由 flowProfile 生成) |
| PRD build(light) | 塞得进单上下文的小 PRD(单 lane,免 scan/plan 扇出;kernel 入口合成 task_map + 铸 run goal;judge.passed 后自动 ship + goal 终态) | `prd-light-v3.yaml` | `prod-runtime/v1` + `PrdFlow topology: light` 展开 |
| Issue fix(default light) | 单一可复现问题；固定 Fix -> Verify -> Judge | `zf flow draft --kind issue` | `IssueFlow` 默认 light，Kernel 合成单 Task Contract |
| Issue fix(explicit fanout) | 跨模块或根因/范围不确定的问题 | `issue-fanout-v3.yaml` | `prod-runtime/v1` + `IssueFlow topology: fanout` 展开 |
| Refactor | 从已有系统重构或迁移到新实现 | `refactor-lane-v3.yaml` | `refactor-controller-runtime/v3` + `RefactorFlow v3` 展开 |
| General | 通用只读证据收集、综合与独立验证 | `general-workflow-v3.yaml` | `prod-runtime/v1` + safe Generic Workflow `evidence-synthesis-v1` |

注:roles 由 flowProfile 展开生成;`common/profiles.yaml` 里的 RoleSet
(`prd-codex-lanes/v1` 等)是可选组合件,v3 入口并不依赖它们。

## OA Semantic Control

OA 权限按 workflow 规模分级，不按“配置里是否有 orchestrator role”推导：

- 6 个 Codex/Claude PRD fanout、Issue fanout、Refactor v3 入口使用
  `semantic_control`，但默认只声明 `plan_candidate: shadow`。正常路径不等待 OA，
  OA 结果只进入对账和指标。
- `prd-fanout-v3-oa-pilot.yaml`、`issue-fanout-v3-oa-pilot.yaml` 和
  `refactor-lane-v3-oa-pilot.yaml` 是非 preferred 的 blocking 样例：root 保持
  `exception_advisor`，仅对应 Product Flow policy 通过显式 `pilot_id` 启用
  `plan_candidate: blocking`。loader 拒绝无 `pilot_id`、General/Research、micro/light、
  未声明 standard/full route 或包含其他 blocking checkpoint 的配置。
- 2 个 PRD light 和 2 个 General v3 入口保持 `exception_advisor`，正常路径不增加
  OA Provider turn，也不要求它们伪造 Plan Artifact Package。
- 固定 `research-fanout` / `research-adaptive` 是 preparatory read-only route。完整
  controller 通过 `flow_policies.research: exception_advisor` 显式隔离；Research
  聚合、结果回传和后续 PRD/Refactor 启动合同不变。

`zf flow draft` 的默认 Issue 为 light；PRD/Refactor（`feat` 仍是 PRD 别名）和
`zf project init --kind multi` 中的完整 Product Flow 只生成 `plan_candidate: shadow`。
动态 General 与 Research 保持 exception advisor。正常 Impl/Verify lane continuation
始终由 Kernel/WRC 机械推进，不增加逐 lane OA turn。

配置可用 checkpoint 中，`pre_impl` 表示消费 Plan Artifact Package/Task Map 的
Plan-bound run-plan/context-route 复核，当前默认控制器不启用。真正的 `run_start` 应只消费
workflow intake/Run Contract，尚未接入配置入口；不得再把 Plan 后的 checkpoint 命名为
`run_start`。

配置覆盖已经分级完成。PRD、Issue、Refactor 的真实 Codex shadow 全链和隔离 General 真实
Verify 已通过，Plan -> Impl -> Verify 必读上下文也完成 digest/Read Ledger 对账；但真实
Provider A/B 仍只有单一 PRD `plan_candidate` 样本。前者证明默认路径兼容性，不能替代完整
shadow/blocking 对比，因此 blocking 仍只允许上述显式 Product Flow pilot，不进入默认路由。

## 技能与执法姿态(2026-07-08 起,以 `common/profiles.yaml` 为准)

- **技能只来自仓内两目录**(2026-07-08 起,agent-skills 外部基线经 1v1
  核实后退役):`skills/` 承载 zf-* 边界/合约技能与 `zf-yoke-*-role-context`
  角色 wrapper(planner / dev-worker / test-evaluator / quality-gate,已接
  入各 stage bundle);`yoke/` 方法论族经 wrapper 的 frontmatter
  `dependencies` 闭包自动物化(tdd-evidence、verify-review、
  vertical-slicing、grill、git-evidence、source-verification 等),briefing
  索引标注 "dependency of";无 wrapper 的 stage(scan / discovery /
  module-parity-scan 等)按需直挂裸 yoke 名,经 yoke overlay 解析。
  `common/profiles.yaml` 使用相对仓库路径 `../../../skills`,避免 controller
  示例绑定某台机器的 checkout 路径;`zf profile bootstrap --apply` 会把启用
  的 skill/yoke 依赖闭包 vendor 到目标项目本地 `skills/`,并把拷贝后的
  profile source 重写为 `skills`。
- **执法档**:flowProfile 展开默认 `schema_profile: canonical-dag/v6`(读者
  子报告证据档:child 完成事件 `non_empty[summary, evidence_refs]` +
  `report.requirement_coverage_matrix` 至少一行);两个 prod 预设开
  `verification.event_schema.mode: blocking`(违约完成事件落盘即换
  `discriminator.failed` 走返工)、`report_evidence_gate: fail_closed`、
  `runtime.skills.strict: true`(启用技能缺失在 validate/start 前暴露)。
- briefing 样例由 FIX-14 教育机制按 schema 规则自动镜像必填字段,合规
  agent 照抄模板即过档。
- **合并候选树质量门(多 lane 必配)**:多 lane fanout_writer 流不配
  `quality_gates` 时,candidate 合成树不经任何验证即进 judge(跨 lane
  偏斜 per-lane verify 原理上不可见,r4 F10)。`zf start`/`zf validate`
  对此 **fail-closed 拒绝**——按各 yaml 内注释模板填项目真实命令
  (typecheck + 单测)启用,或显式豁免
  `workflow.allow_unverified_candidate: true`(观测型运行)。
  单 lane(light)无此风险,保持 WARN。通过 `zf profile bootstrap --apply`
  生成新项目时,若探测器已经得到栈级 gate 命令,会自动写入
  `quality_gates.static.required_checks`,避免生成后立刻被 candidate gate
  拒绝。
- **Goal delivery parity**:原有 8 个 Issue/PRD/Refactor 入口统一
  `deliveryPolicy: ship_candidate`；2 个 General 入口使用 Generic Workflow
  `artifact_delivery/report_only`，两者都经 Goal Completion Gate 形成唯一终态。
  前 8 个入口由 admitted Thin Judge result 形成 completion claim，再由 scoped
  delivery operation 合入 `ship_target_branch`；General 则以独立 verify 后的必需
  artifact claim 收口，不经过 Thin Judge。成功后都只产生一个
  `run.goal.completed`。`auto_ship_on_judge_passed` 仅供 legacy active run 恢复，
  兼容投影不得触发新运行交付。
- **Artifact handoff blocking**:原有 8 个 Issue/PRD/Refactor 入口显式
  `artifactPackageMode: blocking`。新 Run 缺 current Package、Contract、
  TaskRef、target 或 required-read evidence 时 fail-closed；mode 同时固定在
  Run Contract 与 immutable Plan Artifact Package。升级前没有 mode 字段的
  Package 保持 legacy shadow，配置回退也不会改写既有 Package/EventLog。
  General 入口走 Generic Workflow port、Required Read Ledger 和
  `artifact-delivery` semantic submit，不要求伪造 Plan Package。
- **Generic failure ownership**:General 编译产生的 `<stage>.failed` 由 Kernel
  按 `flow_kind=workflow`、`stage_id` 和 Generic Workflow contract digest 注册
  stage-local bounded replan；cold-start event-contract audit 将这些配置内消费者
  计入闭包，Run Manager 不再并发创建 unknown-actionable 诊断。缺失上述合同身份的
  任意自定义 `*.failed` 仍 fail-closed。
- **Role process lifecycle**:标准 Issue / PRD / Refactor 入口通过 common
  `roleDefaults.lifecycle.mode: on_demand` 延迟创建普通 scan、plan、impl、
  verify、judge Provider process，并在机械准入通过后保留 session/worktree
  休眠；configured orchestrator agent 显式 `resident`。两份 PRD light
  入口显式覆盖为 `eager`，保留短流程的低启动延迟和旧配置兼容基线。
  `provider_session` 仍为空即继承 Provider 默认；Codex/Claude 的 effort、
  agent 与并发上限属于 provider-specific override，不进入跨 Provider common
  profile。
- **Active budget breaker**:`bounded-direct-v1` 将每个 provider operation 限制为
  `900s / 60 usage samples / 1,500,000 tokens / $8`，Run 限制为
  `3000s / 10,000,000 tokens / $40`。10 个入口的所有 provider role（包括 resident
  OA）都必须显式或经模板解析到该 profile。operation 上限固定在 immutable request，
  Run 上限与 meter baseline 固定在 `run.admission.admitted`；运行中越界会终止 provider
  并进入 owner-visible blocked，而不是等下一次 dispatch 才检查。usage sample 使用
  去重后的 canonical cost ledger 计数；fanout briefing 要求预留最后两次用于写入和
  submit，未显式要求当前外部事实的任务不得消耗回合做 Web 调研。
- **evidencePolicy 驱动执法**:`evidencePolicy: strict_refs` 由 loader
  派生 `event_schema.mode: blocking` + `report_evidence_gate: fail_closed`
  (单一控制点;显式 `verification.*` 配置优先,是逃生门)。
- **Verify/Gap 职责分离**:lane Task Verify 只加载独立验收、checklist 与
  mechanical claim 方法并返回 typed pass/reject/blocked；全局 rescan、gap
  synthesis 和 task-map amendment 只挂到 discovery、module-parity 与
  verify-bridge。Thin Judge 只加载 goal-closure contract。Refactor 入口使用
  项目中性的 parity scope；provider/webui/tui/memory 等专项能力由 intake
  adapter 根据显式 scope 或项目 Skill 动态追加。

## 组合规则

- `profile_sources` 只在 load/render/start 前解析。
- `uses` 引用 profile name/version,不直接把 profile 文件当 runtime include。
- `Workflow` / `RefactorFlow` 只编译 canonical `workflow.stages`、roles、
  pipelines 和 schema;runtime 仍只消费 expanded `ZfConfig`。
- common profile 放跨 PRD/Issue/Refactor 复用能力;workflow/project 专项事实
  不应混入 common。
- `roleDefaults` 可结构化声明 `providerSession` / `lifecycle`；lane 可通过
  `providerSessionByStage` / `lifecycleByStage` 覆盖 impl、verify 等 stage。
  Provider 私有 argv 不属于该配置面。

## 验证

```bash
uv run zf config render --config examples/prod/controller/prd-fanout-v3.yaml \
  --output /tmp/prd.rendered.yaml --lock /tmp/prd.render-lock.json
uv run zf config render --config examples/prod/controller/prd-light-v3.yaml \
  --output /tmp/prd-light.rendered.yaml --lock /tmp/prd-light.render-lock.json
uv run zf config render --config examples/prod/controller/issue-fanout-v3.yaml \
  --output /tmp/issue.rendered.yaml --lock /tmp/issue.render-lock.json
uv run zf config render --config examples/prod/controller/refactor-lane-v3.yaml \
  --output /tmp/refactor.rendered.yaml --lock /tmp/refactor.render-lock.json
uv run zf config render --config examples/prod/controller/general-workflow-v3.yaml \
  --output /tmp/general.rendered.yaml --lock /tmp/general.render-lock.json
```
