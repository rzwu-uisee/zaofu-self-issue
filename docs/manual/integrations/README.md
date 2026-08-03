# 集成

[English](README.en.md) · [手册首页](../00-index.md)

- [Feishu AI-Native 直连 Bridge](../19-feishu-ai-native-direct-bridge.md): 群聊、单聊、审批和 provider 会话。
- [Feishu Automation / Kanban Sync](../11-feishu-automation-kanban-sync.md): Daily、Weekly、Project 报告与看板投影。
- [Channel 到 PRD](../workflows/channel-to-prd.md): Channel canonical 模型及飞书来源回传。
- [真实 Provider Preflight](../16-real-codex-provider-preflight.md): Codex/Claude CLI、sandbox 与认证边界。

集成负责 transport、projection、intent 和 controlled-action request。它不能直接写
Task、Feature、Workflow 或 Run canonical state，也不能持有第二套业务状态机。
