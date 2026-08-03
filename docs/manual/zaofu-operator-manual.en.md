# ZaoFu Operator Manual: Compatibility Route

> Status: compatibility entry point.
> Last reconciled: 2026-08-03.

This path is retained for existing links. It no longer duplicates the complete operator manual because a
second, monolithic copy drifted from Product Flow, Channel, and runtime authority contracts.

Use the task-oriented [English Manual](00-index.en.md) as the current entry point. Chinese readers should
use the [中文手册](00-index.md).

## Choose the Operation

| Goal | Current guide |
|---|---|
| install and prove one verified delivery | [First Verified Delivery](getting-started/first-verified-delivery.en.md) |
| understand truth, control, and evidence ownership | [Delivery Control Model](concepts/delivery-control-model.en.md) |
| configure a project | [`zf.yaml` Control Plane](02-zf-yaml-control-plane.en.md) |
| turn a Channel discussion into a PRD | [Channel to PRD](workflows/channel-to-prd.en.md) |
| preview, propose, approve, and start a Workflow | [Controlled Workflow Start](workflows/controlled-workflow-start.en.md) |
| understand planning, Task Map, and dispatch | [Plan, Task Map, and Kernel Dispatch](13-plan-task-map-orchestrator-dispatch.en.md) |
| observe Goal, Run, Task, evidence, Trace, Graph, and Loop | [Observe a Delivery](operations/observe-delivery.en.md) |
| recover a stalled long-running Run | [Recover a Long-Running Run](operations/recover-long-running-run.en.md) |
| preserve context and handoff evidence | [Context, Handoff, and Artifacts](operations/context-handoff-artifacts.en.md) |
| operate Supervisor and Autoresearch | [Supervisor](12-supervisor-inspection-usage.en.md) / [Autoresearch](10-autoresearch-usage.en.md) |
| use Feishu integrations | [Integrations](integrations/README.en.md) |
| validate Web and browser E2E | [Web and Observability](06-web-observability-e2e.en.md) |
| diagnose a failure | [Troubleshooting](07-troubleshooting.en.md) |

## Current Architectural Baseline

- `zf.yaml` is the only control-plane configuration and defines `project.state_dir`.
- `events.jsonl` is the append-only occurrence, ordering, causation, verdict, and reference ledger.
- Task, Feature, Session, RoleSession, and TaskAttempt stores own their canonical current state.
- Required artifacts/sidecars own complete semantic bodies and large evidence; events bind them by refs/digests.
- SQLite, Web, Trace, Graph, Loop, cost, diagnostics, and summaries are read projections.
- Product Flow uses Kernel-owned deterministic happy-path dispatch. A configured `orchestrator` role Agent
  handles low-frequency semantic exceptions and proposals; it is not a second state machine.
- Legacy safe-team is an explicit compatibility mode where Layer 2 may decompose and assign work.
- Channel discussion and Workflow execution are separate state machines. Channel creation does not auto-fanout,
  and Finalize/Owner confirm does not auto-start a Workflow.
- Workers report facts, intent, artifacts, and evidence through sanctioned events/actions. They do not directly
  mutate Kernel-managed state.

Current code, tests, and the task-oriented manual are the operator-facing
authority. Historical proposals do not override those executable contracts.

## Operator Safety Baseline

```bash
uv run zf validate --cold-start
uv run zf start
uv run zf kanban --board
uv run zf events --last 50
uv run zf doctor
```

Before declaring a delivery complete, read back Goal/Claim/Task/Evidence coverage, terminal predicates,
required artifacts, Git evidence, tests, and projection freshness. Agent prose or a running tmux pane is not
completion evidence.
