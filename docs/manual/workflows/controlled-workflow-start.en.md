# Controlled Workflow Start

[中文](controlled-workflow-start.md) · [Workflow index](README.en.md)

> One Task-bound Workflow product capability is shared by Web, Kanban Agent,
> Channel, Feishu, and CLI through the same route catalog, proposal, and approval
> contract.

## Prerequisites

Before starting a Workflow, you need:

- a real tracked Task;
- the current Project's `zf.yaml` and active route catalog;
- a Task/PRD/issue/refactor source that explains the objective, inputs, and expected output;
- artifacts, profiles, roles, and provider capabilities required by the route;
- operator authorization, required only for the actual apply step.

A Project, Channel, or chat message is not a Task.

## Query Available Routes

```bash
zf workflow routes --task TASK-ID --format json
```

A route must come from the active Project catalog. A Planner may recommend a
route and parameters from the request, but it cannot invent stages, roles,
writers, or Kernel primitives from chat text.

Common families include PRD, Issue, Refactor, Research, and registered Generic
Workflows. The current command output is authoritative for availability.

## Preview, Propose, Apply

Read-only preview:

```bash
zf workflow start \
  --task TASK-ID \
  --route ROUTE-ID \
  --objective "The explicit objective for this Run" \
  --parameters-json '{"expected_output":"verified delivery"}' \
  --preview \
  --format json
```

Create a durable proposal:

```bash
zf workflow start \
  --task TASK-ID \
  --route ROUTE-ID \
  --objective "The explicit objective for this Run" \
  --parameters-json '{"expected_output":"verified delivery"}' \
  --propose \
  --format json
```

Apply only after an operator reviews the exact proposal:

```bash
zf workflow start \
  --proposal-event-id EVENT-ID \
  --authorization-ref APPROVAL-REF \
  --authorization-token "$ZF_WORKFLOW_ACTION_TOKEN" \
  --apply \
  --format json
```

Provider agents must not read or receive `ZF_WORKFLOW_ACTION_TOKEN`.

## Web And Kanban Agent Path

```text
existing Task
  -> Kanban Agent clarifies objective and inputs
  -> active route options
  -> user selects, Chat about, or Customize
  -> Plan fixes the exact option
  -> independent Approve card
  -> Start workflow
  -> workflow.invoke.requested
```

![Task-bound Workflow from planning and selection to independent approval and ignition](../assets/quickstart-direct-workflow.webp)

A Plan is not approval. `Continue` or route selection creates the exact
proposal; external effects still require independent authorization.

## Current Dynamic-Workflow Boundary

Currently supported:

- controlled synthesis from Requirement to immutable proposal, effective config, and Run;
- registered PRD, Issue, Refactor, Research, and related routes;
- Generic Workflow static-safe v1 over registered safe primitives, DAG barriers, and artifact completion;
- an opt-in bounded, read-only provider-native adaptive Research root;
- Run evolution through replan/proposal when scope, Tasks, ACs, or the next action change.

Not currently claimed:

- arbitrary agent code hot-plugged into the Kernel;
- unrestricted writer-topology hot reload inside an active Run;
- arbitrary dynamic writers or partial checkpoints enabled by default;
- provider-native child graphs bypassing the root TaskContract, Verify, or completion gate.

When project semantics change, an Agent produces a new plan, artifact, or
proposal. The Kernel owns schema, identity, admission, currentness, permission,
replay, and effects.

## Observe And Diagnose

```bash
zf workflow inspect
zf workflow audit
zf workflow gates
zf task trace TASK-ID
zf events --last 80
```

When start is rejected, check:

- the Task exists and is current;
- the route is active;
- the proposal event matches the Task, route, and parameters;
- approval ref/token authorizes only that exact proposal;
- required input refs and provider capabilities are available;
- no incompatible Project Run is already active;
- admission produced explicit diagnostics.

## Definition Of Done

Workflow Start is complete when the exact proposal is authorized and one
`workflow.invoke.requested` is bound to the same Task, route, and parameters.
It does not mean software delivery is complete. Run, Verify, Closure, and the
Completion Gate decide that.

## Related

- [Project, bootstrap, and Workflow start](../20-project-bootstrap-workflow-ignition.en.md)
- [Plan, Task Map, and dispatch](../13-plan-task-map-orchestrator-dispatch.en.md)
- [Observe a delivery](../operations/observe-delivery.en.md)
