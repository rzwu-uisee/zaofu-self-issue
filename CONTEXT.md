# ZaoFu Domain Language

ZaoFu 以确定性 Kernel 管理状态与副作用，并通过投影向用户展示外部系统和运行时事实。

## Issue Triage

**External Issue**:
官方 Forge 仓库中的 Issue；不包含 Pull Request。
_Avoid_: Triage record, local issue

**Issue Mirror**:
ZaoFu 从 External Issue 构建的可重建只读镜像；它不是独立业务权威。
_Avoid_: Canonical issue, local triage decision

**Derived Triage Group**:
只由 External Issue 的 provider state 与 labels 机械计算的展示分组。
_Avoid_: Triage stage, workflow state

**Runtime Intervention**:
ZaoFu 对运行时 proposal、blocked、failed 或 stale 状态的处置领域，与 Issue Triage 相互独立。
_Avoid_: Issue triage

**Issue Intake Task**:
由一个 External Issue 建立的 canonical、只读分诊 Task；它的完成条件是产生可审查的修复候选合同，而不是完成代码修复。
_Avoid_: Fix task, mirrored issue

**Fix Admission**:
操作者对当前 External Issue revision 所绑定 exact Workflow proposal 的授权；只有它允许启动具备代码写入能力的 Fix Run。
_Avoid_: Label trigger, automatic fix

**Verified Candidate**:
已在隔离 worktree 中通过当前 TaskContract 和配置 gate 的本地代码候选；它不代表已合并、已推送或已部署。
_Avoid_: Done, merged fix, published PR
