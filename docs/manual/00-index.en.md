# ZaoFu User Manual

[中文](00-index.md)

> For ZaoFu users and operators. This page routes by the outcome you need; it
> does not require reading the architecture or the full CLI catalog first.
> Current behavior is verified against code, tests, and this manual's executable checks.

## Choose Your Path

| You want to... | Start here | You are done when... |
|---|---|---|
| Install ZaoFu and complete one verified delivery | [First verified delivery](getting-started/first-verified-delivery.en.md) | A Task enters a Workflow and Delivery explains its result and evidence |
| Turn an unclear request into an owner-confirmed PRD with people and agents | [Channel to PRD](workflows/channel-to-prd.en.md) | The owner confirms the canonical PRD and receives a source receipt |
| Select and start a Workflow for an existing Task | [Controlled Workflow start](workflows/controlled-workflow-start.en.md) | The exact proposal is approved and emits `workflow.invoke.requested` |
| Decide whether a long-running goal is actually complete | [Observe a delivery](operations/observe-delivery.en.md) | Goal, Claims, Tasks, Evidence, Gaps, and Closure explain the result |
| Recover a stalled, failed, or no-progress Run | [Recover a long-running Run](operations/recover-long-running-run.en.md) | The Run advances or converges with evidence to blocked/failed/cancelled |
| Understand context and evidence inheritance across agents | [Context, Artifacts, and Handoff](operations/context-handoff-artifacts.en.md) | You can locate the current contract, required reads, result, and lineage |
| Connect Feishu, Automations, or a provider | [Integrations](integrations/README.en.md) | External surfaces use projections and controlled actions without creating another truth |
| Look up commands, configuration, or stable contracts | [Reference](reference/README.en.md) | The generated command inventory or a focused reference gives the current entrypoint |
| Develop, review, or validate ZaoFu itself | [Architecture overview](architecture.en.md) | You can separate Kernel, Agent, Store, Artifact, Projection, and orchestration modes |

## One Product Model

```text
Requirement
  -> confirmed Goal and Claims
  -> approved Workflow and Task Map
  -> contracted Agent attempts
  -> independent verification and evidence
  -> Goal Closure and owner-visible delivery
  -> bounded recovery or explicit terminal blocker
```

Project, Channel, Task, Workflow, Run, and Delivery are first-class product
objects. Graph, Trace, Loop, SQLite, and Web summaries are query and presentation
surfaces, not another scheduler.

## Browse By Subject

- [Getting started](getting-started/README.en.md)
- [Concepts](concepts/README.en.md)
- [Workflows](workflows/README.en.md)
- [Operations](operations/README.en.md)
- [Integrations](integrations/README.en.md)
- [Reference](reference/README.en.md)
- [Showcases](showcases/README.en.md)

## Stable Topic Paths

Existing high-traffic numbered pages remain available. New readers should use
the outcome routes above first.

- [01 Complete Quick Start](01-quickstart.en.md)
- [02 `zf.yaml` control plane](02-zf-yaml-control-plane.en.md)
- [03 CLI operations](03-cli-operations.en.md)
- [04 Harness runtime](04-harness-runtime.en.md)
- [05 Skills, workdirs, and Git evidence](05-skills-workdirs-git-evidence.en.md)
- [06 Web Dashboard](06-web-observability-e2e.en.md)
- [07 Troubleshooting](07-troubleshooting.en.md)
- [08 Create Tasks, Assignment Intent, and Agent Collaboration](08-new-task-agent-squad.en.md)
- [09 CLI usage reference](09-zaofu-cli-usage.en.md)
- [10 Autoresearch](10-autoresearch-usage.en.md)
- [11 Feishu Automation, Kanban, and Project collaboration groups](11-feishu-automation-kanban-sync.en.md)
- [12 Supervisor inspection](12-supervisor-inspection-usage.en.md)
- [13 Plan, Task Map, and dispatch](13-plan-task-map-orchestrator-dispatch.en.md)
- [14 Delivery Trace](14-delivery-trace-usage.en.md)
- [15 Channel collaboration](15-channel-collaboration.en.md)
- [16 Real-provider preflight](16-real-codex-provider-preflight.en.md)
- [18 Product fanout E2E](18-product-fanout-real-e2e.en.md)
- [19 Feishu AI-Native Bridge, live conversations, and approvals](19-feishu-ai-native-direct-bridge.en.md)
- [20 Project, bootstrap, and Workflow start](20-project-bootstrap-workflow-ignition.en.md)

## Documentation Status

A design title alone does not prove a capability exists. Every current
capability must have a user guide, a code entrypoint, and test evidence in the
[capability coverage catalog](reference/capability-coverage.en.md). Maintenance and
release rules live in the
[documentation currentness policy](reference/documentation-currentness.en.md).
