# ZaoFu User Manuals

This directory contains ZaoFu's user-facing documentation. The manuals are
organized from architecture and onboarding through operations, observability,
integrations, and evaluation.

For installation onboarding, Add/Open Project, Kanban Agent, and the first
controlled Workflow, start with [01 Quick Start](01-quickstart.en.md). Use the
[ZaoFu Operator Manual](zaofu-operator-manual.en.md) for a consolidated
reference or the topic index below for focused coverage.

## 0. Architecture

- [Architecture Overview](architecture.en.md) - the three-layer model,
  kernel truth, the `zf.yaml` control plane, and the delivery lifecycle.

## 1. Getting Started

- [01 Quick Start](01-quickstart.en.md) - installation onboarding, Add/Open
  Project, Project Brief and Stack, Channel, Research, and the first Task
  Workflow.
- [20 Project Creation, Bootstrap, and Workflow Ignition](20-project-bootstrap-workflow-ignition.en.md) -
  the complete multi-kind Project, `zf.yaml` and Bootstrap boundary, Task,
  approval, CLI, and Web path.

## 2. Core Operation

- [02 `zf.yaml` Control Plane and Runtime State](02-zf-yaml-control-plane.en.md)
- [03 CLI Operations](03-cli-operations.en.md)
- [04 Harness Runtime](04-harness-runtime.en.md)
- [05 Skills, Workdirs, and Git Evidence](05-skills-workdirs-git-evidence.en.md)
- [07 Troubleshooting](07-troubleshooting.en.md) - common failures, WebKanban
  launch drift, and diagnostics
- [09 ZaoFu CLI Reference](09-zaofu-cli-usage.en.md)

## 3. Planning, Observation, and Diagnosis

- [06 Web, Observability, and E2E](06-web-observability-e2e.en.md) - canonical
  WebKanban launcher, runtime observation, and E2E entry points
- [08 New Task, Agent, and Squad](08-new-task-agent-squad.en.md)
- [12 Supervisor Inspection](12-supervisor-inspection-usage.en.md)
- [13 Plan, Task Map, and Orchestrator Dispatch](13-plan-task-map-orchestrator-dispatch.en.md)
- [14 Delivery Trace](14-delivery-trace-usage.en.md)

## 4. Feishu and Channel Collaboration

- [19 Feishu AI-Native Direct Bridge](19-feishu-ai-native-direct-bridge.en.md)
- [15 Channel Collaboration](15-channel-collaboration.en.md) - Kanban Plan
  creation of Channels and members, template discussion, continuation, Task
  proposals, and Feishu projection.
- [11 Feishu Automation and Kanban Sync](11-feishu-automation-kanban-sync.en.md)

The legacy OpenClaw Feishu forwarding path is deprecated. Use the direct
bridge documented in manual 19.

## 5. Autoresearch and Real E2E

- [10 Autoresearch](10-autoresearch-usage.en.md)
- [Autoresearch Orchestrator](autoresearch-orchestrator.en.md)
- [Autoresearch Campaign](autoresearch-campaign.en.md)
- [16 Real Codex Provider Preflight](16-real-codex-provider-preflight.en.md) -
  sandbox preflight, trusted-local launcher, and safety boundaries
- [18 Product Fanout Real E2E](18-product-fanout-real-e2e.en.md)
