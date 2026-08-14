# Changelog / 版本记录

本文件记录 ZaoFu 的公开版本。完整发布说明以对应版本文档和 GitHub/GitLab
Release 页面为准；内部研发标签与运行记录不构成公开兼容承诺。

## Unreleased

- 暂无。

## v0.0.4 - 2026-08-14

Release notes: [中文](docs/releases/v0.0.4.md) |
[English](docs/releases/v0.0.4.en.md)

Developer Preview 运行时权威与恢复版本，将 v0.0.3 后的 Task Pipeline、成本预算、
Task Contract Authority 和 Kanban 可观测性改动收口为公开 release。

### Added

- Task Pipeline orchestration runtime、Task workspace、Stage Worker、Candidate integration
  和 Orchestrator Agent 受控语义 checkpoint。
- Provider usage / pricing catalog / hard budget enforcement、Long-Run Truth、Recovery Case
  和 retention inventory。
- Task Contract Authority、current execution target、stale result fail-closed 和对应 doctor。
- Kanban Agent turn delta/timing observability、Web cost/resource projections 和 interaction policy。

### Changed

- Goal Closure、Reader/Writer fanout、workflow resume/rework 和 terminal convergence 统一绑定
  current generation、artifact ref、digest 与 execution target。
- Web Dashboard 更清晰地区分 current truth、历史 attempt、raw event 和需要关注的终态事实。

### Validation

- 分批 focused validation：268 passed、627 passed、261 passed、198 passed / 1 skipped。
- Web unit validation 覆盖 Channel action、Kanban session、cost precision、task display、
  page load policy 和 Kanban Agent interaction policy。

## v0.0.3 - 2026-08-08

Release notes: [中文](docs/releases/v0.0.3.md) |
[English](docs/releases/v0.0.3.en.md)

Developer Preview Feishu 协作版本，将 v0.0.2 的 Channel、PRD、Task 和受控 Workflow
路径延伸到 Project 级 Feishu 协作群。

### Added

- Project-scoped Feishu collaboration group binding、成员回读、route index 和 workspace
  bridge lease。
- Kanban Agent / Run Manager 的群消息路由、项目状态回复、Plan 审批卡、进度卡和交付卡。
- Feishu Project group、bridge lease、catch-up、stream convergence 和 controlled workflow
  start 的回归测试。

### Changed

- Feishu 入站消息、卡片回调和流式回复统一回到 EventWriter、controlled action 和
  auditable artifact 边界。
- 文档补充 Feishu AI-native direct bridge、Automation / Kanban sync、CLI command index 和
  capability coverage。

### Validation

- 目标 Feishu / Project group 回归：209 passed。

## v0.0.2 - 2026-08-05

Release notes: [中文](docs/releases/v0.0.2.md) |
[English](docs/releases/v0.0.2.en.md)

Developer Preview 稳定性版本，重点闭合 Channel 到 PRD、三类 Ask User Question、
任务交接、长程恢复和 Goal Closure 的真实运行缺口。

### Added

- Kanban、Channel 和 Workflow 共用的持久化 Ask User Question 交互，包括分批回答、
  revision/digest CAS、刷新恢复和 stale submission 拒绝。
- Channel 多角色讨论到 Owner-confirmed PRD、Task provenance 和受控 Workflow proposal
  的完整产品路径。
- 面向真实产品交付的 Codex + Docker Playwright E2E 证据包。

### Changed

- Writer、Reader、fanout、semantic replan 和 candidate handoff 统一使用当前 attempt、
  revision、lineage、checkpoint 与 source/contract 引用。
- Goal completion、verification discovery 和 Goal Dossier 只消费当前运行权威证据，
  避免历史或其他 Run 的信号污染终态。
- Web Channel/Workflow 问卷、Loop 投影和运行诊断交互更加一致。

### Fixed

- Channel 路由竞争、重复问题、终态失败重复投递和 PRD delivery admission 缺口。
- 丢失 dispatch 的 redrive、provider generation fence、stale contract、terminal replay、
  replan continuation、task-ref repair 和恢复预算问题。
- pytest workspace 预热、Web event cache、Codex hook 环境和 Autoresearch subprocess
  mock 的测试隔离问题。

### Validation

- 当前 `dev` 确定性测试：9261 passed、19 skipped、18 deselected。
- 串行测试 10 passed；Web typecheck/unit、49 个 pre-merge sentinel 和 7 个 flow smoke
  均通过。
- 真实 Codex 候选产品：17 个产品测试、构建和 Docker Playwright 11/11 通过。

### Known Limitations

- 完整 release run 仍需独立人工 WCAG 证据，Agent 不会代签人工验收。
- 目标项目 Playwright gate 需要显式绑定 Docker runner 或声明宿主依赖。
- 已知一个低风险 favicon 404；Observability 页面仍是较慢的 Web 页面。
- OpenCode provider SPI 在本版本仍是设计，不是已发布实现。

## v0.0.1 - 2026-07-30

Release notes: [中文](docs/releases/v0.0.1.md) |
[English](docs/releases/v0.0.1.en.md)

首个公开 Developer Preview。

### Added

- PRD、Issue 与 Refactor 产品 Controller。
- Codex 与 Claude Code 多 Agent、lane fanout/fan-in 和持久化接力。
- 外层 lane 与 provider-native sub-agent 组合的受控双层蜂群执行。
- TaskContract、独立 Verify、Thin Judge 和证据门控 Goal Closure。
- Supervisor、Run Manager、Autoresearch 与 controlled action 恢复闭环。
- Web Dashboard、CLI、Kanban Agent、Channel、Inbox 与 Feishu/ChatOps。
- Artifact、sidecar、EventLog、canonical stores 和 SQLite read model 分层真相。

### Status

- Developer Preview；1.0 前公共 API、事件 schema、Controller profile 和部分 CLI
  仍可能调整。
- 真实运行依赖目标项目 quality gates，以及已认证的 Codex CLI 或 Claude Code CLI。
