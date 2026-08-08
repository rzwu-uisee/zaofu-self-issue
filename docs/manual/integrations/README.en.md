# Integrations

[中文](README.md) · [Manual home](../00-index.en.md)

- [Feishu AI-Native direct bridge](../19-feishu-ai-native-direct-bridge.en.md): group chat, direct chat, Project auto-provisioning, approvals, and provider sessions.
- [Feishu Automation / Kanban sync](../11-feishu-automation-kanban-sync.en.md): Daily, Weekly, Project reports, board projections, and the Project collaboration-group lifecycle.
- [Channel to PRD](../workflows/channel-to-prd.en.md): the Channel canonical model and source receipts.
- [Real-provider preflight](../16-real-codex-provider-preflight.en.md): Codex/Claude CLI, sandbox, and authentication boundaries.

An integration owns transport, projection, intent, and controlled-action
requests. It cannot write Task, Feature, Workflow, or Run canonical state
directly and cannot maintain another business state machine.
