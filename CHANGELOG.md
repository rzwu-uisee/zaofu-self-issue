# Changelog / 版本记录

本文件记录 ZaoFu 的公开版本。完整发布说明以对应版本文档和 GitHub/GitLab
Release 页面为准；内部研发标签与运行记录不构成公开兼容承诺。

## Unreleased

- 暂无。

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
