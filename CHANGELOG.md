# Changelog / 版本记录

本文件记录 ZaoFu 的公开版本。完整发布说明以对应版本文档和 GitHub/GitLab
Release 页面为准；内部研发标签与运行记录不构成公开兼容承诺。

## Unreleased

- v0.0.4 发布草案：[中文](docs/releases/v0.0.4.md) |
  [English](docs/releases/v0.0.4.en.md)。本草案聚焦 Task 权威、Product Acceptance、
  Long-Horizon 恢复、Provider 成本与 Web 可观测性；Task Pipeline v4 和 Orchestrator Agent
  仍是受控试点。当前包元数据仍为 `0.0.2`，且 v0.0.3 尚未打 tag。
- v0.0.3 发布草案：[中文](docs/releases/v0.0.3.md) |
  [English](docs/releases/v0.0.3.en.md)。当前包元数据仍为 `0.0.2`；完成版本升级、最终验证
  和 tag 前，这不是已发布版本。

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
