# Issue Verified Candidate 人工交付与 Triage 控制批次

> 状态: active

## 已批准范围

- 以 `rzwu-uisee/zaofu-self-issue` 的 `dev` 为代码交付目标；
- Verified Candidate 经 Owner 人审后生成本地不可变 review branch 和人工 PR handoff；
- 人工 push/建 PR/GitHub 审核/merge，ZaoFu 只读记录与同步；
- 补齐 Triage Pause/Resume/Permanent Cancel 与不可恢复警告；
- Issue 点击反选、label/tool 按钮提示、超过三个 label 折叠、列表和详情 label 点击筛选。

## 验收标准

1. 配置独立声明 code delivery repository/dev → verify: schema/loader tests green。
2. exact verified candidate 可 Owner approve/change-request/reject → verify: service + API stale/idempotency/auth tests green。
3. approved candidate 可准备本地 review ref 和 handoff 且绝不 push → verify: git fixture checks ref/SHA、base drift、dirty tree、remote refs unchanged。
4. 人工 PR URL 可登记并只读同步到 merged → verify: GitHub response fixtures cover wrong repo/base/head/open/review/closed/merged。
5. Triage 显示 Candidate Review 与 PR lifecycle → verify: frontend unit/typecheck/build + API projection tests green。
6. queued/active/paused Run 控制与 fix_cancelled 投影正确 → verify: projection/action tests green。
7. 永久取消提示醒目且要求确认，不回滚提示明确 → verify: component/browser assertion green。
8. Issue 可再次点击反选；label tooltip、>3 折叠悬停展开、列表/详情点击筛选、Refresh/Start Triage tooltip 可用 → verify: model/component/browser tests green。
9. 跨 Event/config/Web/git 边界闭包通过 → verify: `scripts/dev-premerge-gate.sh`、`scripts/dev-verify.py run` 与相关 mock E2E green。

## 非目标

- 自动 push、自动 PR、自动 merge、自动关闭/评论 Issue；
- GitLab adapter；
- 部署或重启生产服务。

## 验证记录

- `uv run pytest tests/test_structure_discipline.py::test_00_index_links_resolve_to_existing_files tests/test_issue_candidate_delivery.py tests/test_issue_triage_mirror.py tests/test_external_issue_ingress.py tests/test_self_issue_workspace_policy.py -q --no-cov` → 33 passed；
- `uv run pytest tests/test_memory_rotate_concurrency.py tests/test_session_mutex.py tests/test_state_locks.py -q --no-cov -m 'serial and not perf and not host and not real_provider'` → 10 passed；
- `bash scripts/run-flow-smoke.sh` → 10 passed；
- `npm run test && npm run build` → typecheck、token lint、unit、bundle budget、production build 全部通过；
- Docker `mcp/playwright:latest` 执行 `issue-triage-controls.spec.ts` → 2 passed；
- broad deterministic → 10964 passed、19 skipped；34 个失败均不涉及本批次文件，属于当前 v7 已有缺失资产、host/并行环境或冻结基线；
- `scripts/dev-premerge-gate.sh` → 50 passed；唯一失败为当前 HEAD 已存在的 `web/src/app/App.tsx` 3673 行超过冻结上限 3580，本批次未修改该文件，未通过抬高上限掩盖；
- `uv run zf validate --cold-start` → cold-start 5/5、event contract 0 errors；仅因主机缺少 `claude` 命令导致 provider readiness 失败。
