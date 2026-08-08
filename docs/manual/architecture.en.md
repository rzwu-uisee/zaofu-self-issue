# ZaoFu Architecture Overview

[中文](architecture.md) · [Concept index](concepts/README.en.md)

> For operators and contributors who need runtime boundaries. First-time users
> should begin with the
> [first verified delivery](getting-started/first-verified-delivery.en.md).
> This page follows current code and tests and does not generalize the
> historical three-layer safe-team model.

## 1. Product Position

ZaoFu is an AI Agent Delivery Control Plane and Coding Harness for long-horizon
software delivery. It brings Goal, Task, Agent, code, tests, evidence, recovery,
and human decisions into one observable, recoverable, auditable delivery system.

ZaoFu does not replace Codex, Claude Code, or another provider agent. Providers
keep semantic judgment, code implementation, and tool execution. ZaoFu owns the
deterministic runtime boundary and team delivery state.

```text
idea / PRD / issue / refactor
  -> intake / confirmed goal
  -> approved workflow / task map
  -> contracted agent attempts
  -> independent verification
  -> goal closure / completion gate
  -> owner delivery or bounded terminal blocker
```

![Animated overview of Project, Workflow, Delivery, and the Recovery Loop](assets/concept-delivery-control-loop.webp)

## 2. Five Architecture Pillars

ZaoFu is a delivery system built from five engineering mechanisms rather than
a loose collection of Agents:

| Pillar | Problem it solves | Current boundary |
|---|---|---|
| Goal Engineering | Fix a requirement as Goal, Claims, Acceptance, Non-goals, and completion definition | Task done does not automatically close the Goal |
| Graph Engineering | Compile a Goal into Workflow, Task Map, dependencies, waves, fanout, barriers, and evidence producers | Agents produce the semantic graph; the Kernel executes only an admitted graph |
| Swarm Engineering | Let roles, providers, and Workers research, implement, verify, and aggregate in parallel | Bounded fanout/fan-in; no unlimited recursive recruitment or state-machine bypass |
| Loop Engineering | Converge through verification, rework, replan, recovery, and completion | Every loop has attempt, budget, evidence, and termination bounds |
| Harness Engineering | Govern context, tools, worktrees, events, state, artifacts, security, and observability | Provider Agents never own canonical state |

Together they answer what to build, how to decompose it, who should execute it,
how it should converge, and what proves completion. Graph, Swarm, and Loop all
operate inside the same Harness authority boundary rather than creating their
own task truth.

## 3. Layered Runtime Authority

ZaoFu is neither pure event sourcing nor a system where one JSON file owns every
fact.

| Layer | Carrier | Authority | Legal writer |
|---|---|---|---|
| Control plane | `zf.yaml` | topology, roles, policy, budget, safety, and `project.state_dir` | human or controlled config tool |
| Occurrence ledger | `events.jsonl` plus archive segments | occurrence, ordering, causation, verdict, and refs | sanctioned `EventWriter` / `EventLog` path |
| Current state | Task, Feature, Session, RoleSession, TaskAttempt stores | current status, assignment, lease, and attempt | Kernel store/helper |
| Complete semantics | artifacts, sidecars, accepted packages | plans, Task Maps, results, evidence, and large payloads | atomic sanctioned writer |
| Query projection | SQLite, Trace, Graph, Loop, Web summaries | query, aggregation, visualization, and freshness | projector/read-model builder |
| Active transport | SSE, LiveDeltaBus, provider streams | ephemeral deltas | transport/runtime |

Key boundaries:

- an event may prove an action occurred and reference its result while the full body lives in a sidecar;
- TaskStore answers current Task state while EventLog proves a transition and its causation;
- a required artifact is not a disposable projection;
- SQLite, Graph, Trace, and Web pages are rebuildable and cannot write scheduler truth;
- not every canonical store can currently be rebuilt from `events.jsonl` alone.

## 4. Semantic Decision And Deterministic Execution Planes

ZaoFu separates open-ended Agent judgment from reliable Runtime execution:

```mermaid
flowchart LR
  subgraph control["Control Plane + Admitted Semantics"]
    direction TB
    CONFIG["zf.yaml<br/>topology / roles / policy / budget / state_dir"]
    PREFLIGHT["Load / Validate / Preflight<br/>Frozen Effective Config"]
    SURFACE["CLI / Web / Feishu / Channel / Kanban Agent"]
    INTENT["Requirement / PRD / Issue / Refactor"]
    SEMANTIC["Agent + Skill<br/>Goal / Plan / FlowSpec / Task Map"]
    PROPOSAL["Typed Proposal / Artifact"]
    HOLD{"Approval<br/>required?"}
    OWNER["Preview / Approve / Reject"]
    ACCEPTED["Accepted Artifact<br/>Run Contract / Generation"]
    CONFIG --> PREFLIGHT
    SURFACE --> INTENT --> SEMANTIC --> PROPOSAL --> HOLD
    HOLD -- "yes" --> OWNER
    OWNER -- "approve" --> ACCEPTED
    HOLD -- "no" --> ACCEPTED
  end

  subgraph kernel["Deterministic Kernel Runtime"]
    direction TB
    EVENTS["EventWriter / EventLog<br/>append occurrence / causation / verdict / ref"]
    WATCH["EventWatcher<br/>wake-worthy event / periodic tick"]
    ORCH["WorkflowRuntimeCoordinator.run_once()"]
    ROUTE["Registered Topology / Profile<br/>dependency / readiness / WIP / barrier"]
    ADMIT["Mechanical Admission<br/>schema / identity / currentness / scope / budget"]
    ATTEMPT["WorkflowOperation / TaskAttempt<br/>generation / lease / dispatch"]
    TERMINAL["Completion Gate<br/>or Bounded Terminal Disposition"]
    EVENTS --> WATCH --> ORCH --> ROUTE
    ROUTE -- "dispatch" --> ADMIT --> ATTEMPT
    ROUTE -- "close / block" --> TERMINAL
  end
  ACCEPTED -- "accepted event / ref" --> EVENTS
  PREFLIGHT -- "effective config" --> ROUTE

  subgraph edge["Provider Execution Edge"]
    direction TB
    TRANSPORT["tmux / stream-json / Provider Transport"]
    WORKER["Contracted Role Worker<br/>briefing + required context"]
    RESULT["Typed Result / Evidence<br/>zf emit -> next EventLog occurrence"]
    TRANSPORT --> WORKER --> RESULT
  end
  ATTEMPT --> TRANSPORT

  subgraph authority["State Authority And Rebuildable Projections"]
    direction TB
    STORES["Canonical Stores"]
    ARTIFACTS["Artifacts / Sidecars"]
    PROJECTIONS["Trace / Graph / Loop / SQLite"]
    DELIVERY["Web / CLI / Feishu Delivery"]
    STORES --> PROJECTIONS
    ARTIFACTS --> PROJECTIONS
    PROJECTIONS --> DELIVERY
  end
  ORCH -. "Store helpers" .-> STORES
  RESULT -. "complete body / evidence" .-> ARTIFACTS
  EVENTS --> PROJECTIONS
  TERMINAL --> DELIVERY

  subgraph recovery["Exception And Recovery Side Path"]
    direction TB
    ADVISOR["Semantic Exception<br/>Agent triage / replan proposal<br/>re-enters after next-generation admission"]
    SUPERVISOR["Supervisor"]
    RM["Run Manager"]
    ACTION["ControlledActionService<br/>sanctioned event / new attempt / post-verify"]
    AUTO["Autoresearch<br/>diagnosis / repair proposal"]
    SUPERVISOR --> RM --> ACTION
    RM -- "Complex diagnosis" --> AUTO
  end
  ROUTE -. "semantic gap" .-> ADVISOR
  EVENTS -. "stall / operational signal" .-> SUPERVISOR
```

Human approval in this diagram is a profile/policy branch rather than a fixed
stage in every Workflow. The deterministic runtime class is now named
`WorkflowRuntimeCoordinator`; `Orchestrator` remains a compatibility alias for
the same class. Current code includes two default-off
execution extensions: configured OA semantic checkpoints and PRD/Issue/Refactor
Task Pipeline v4 canaries. Both reuse the same Kernel, EventLog, Stores,
Artifacts, and controlled-action boundary instead of creating another state
machine. Worker results and approved recovery actions can re-enter the next
EventWatcher reconciliation only through sanctioned event/artifact paths.

| Actor | Current responsibility |
|---|---|
| Kernel / Python `WorkflowRuntimeCoordinator` | config loading, identity, mechanical dispatch, schema/gates, replay, transitions, and external effects; `Orchestrator` is a compatibility alias |
| Worker Agents and Skills | planning, implementation, review, verification, diagnosis, and product judgment; report typed results or intent only |
| configured `orchestrator` role Agent | defaults to `exception_advisor`; an explicit `semantic_control` profile may make shadow/blocking judgments at registered checkpoints, but it never owns dispatch, Task/Run state, or external effects |
| Supervisor | observe, correlate, and raise attention; no direct repair |
| Run Manager | own operational liveness, choose bounded recovery, and require post-verification |
| Autoresearch | reproduce repeated harness fingerprints and propose isolated diagnosis/repair |
| ControlledActionService | apply approved, auditable deterministic side effects |
| Web / CLI / Feishu | read projections, submit intent, and request token-gated controlled actions |

The deterministic Python `WorkflowRuntimeCoordinator` and an Agent role named
`orchestrator` are different objects. Current code keeps the former as the
Product Flow happy-path coordinator. The OA P0-P15 harness is wired, but its real canary is
still on HOLD. Projects without explicit configuration continue to use
`exception_advisor`; a canary blocking checkpoint is not a production default.

## 5. Two Orchestration Modes

### Product Flow

For PRD, Issue, Refactor, Research, and long-horizon product delivery:

```text
typed event/artifact
  -> Kernel topology/profile route
  -> deterministic WIP/role/worker dispatch
  -> Worker result/evidence
  -> mechanical gate/reducer
  -> exceptional semantic triage/replan proposal
  -> ControlledActionService applies an approved action
```

The Kernel owns happy-path dispatch from the approved topology/profile. Agents
decide project semantics, plans, and solutions. The Kernel does not hard-code
acceptance, scan methods, or product judgment in Python.

### Legacy Safe-Team

An explicit compatibility profile may let a Layer 2 Agent decompose goals,
synthesize contracts, and assign before Kernel validation and mechanical
transition. This is useful for compatibility, teaching, or manual orchestration;
it is not global Product Flow ownership.

Documentation, tests, and extensions must name the mode they target.

## 6. Controlled Multi-Agent And Swarm Execution

ZaoFu supports swarms as a **bounded, typed, observable swarm**. Multiple
Agents can work in parallel, verify independently, and aggregate results while
every child, Task, and attempt retains identity, scope, budget, context, and
evidence. The Kernel remains the only scheduling state machine.

### Current Support Matrix

| Capability layer | Current status | Runtime boundary |
|---|---|---|
| Multi-role Channel Group | Implemented | People and Agents use natural conversation or explicit `multi_lens`; Owner confirms the PRD, and discussion never auto-creates a Task or starts a Workflow |
| Reader fanout/fan-in | Implemented | Read-only roles research or scan in parallel and aggregate through `wait_for_all` or a synth contract |
| Writer fanout | Implemented | Independent Task Map tasks use isolated branches/worktrees; conflict, scope, and candidate admission fail closed |
| Task Map waves and lanes (v3) | Implemented and still default | The Kernel releases work from dependencies, waves, WIP, and currentness rather than a lead Agent manually dispatching every step |
| Static replicas and compatible role autoscaling | Implemented, configuration required | `zf.yaml` sets bounds; Runtime uses ready Tasks, cooldown, and worker health, while a dirty worktree blocks retirement |
| On-demand Worker lifecycle | Implemented, provider resume support required | Dormant roles activate before dispatch and may suspend after settlement and idle admission |
| Cross-provider collaboration | Implemented | Codex, Claude Code, and other configured backends can be assigned by role; independent verification does not trust implementer prose |
| Provider-native compound children | Opt-in Research pilot | Only the root is a ZaoFu protocol actor; the current pilot is read-only, depth one, and limited to four children that cannot create canonical Tasks |
| OA semantic checkpoints | Harness implemented, release HOLD | Only explicit profiles enable them; normal Task handoff adds no OA turn and the Kernel keeps sole dispatch authority |
| Task-centric elastic Stage Worker Pool (v4) | Implementation complete, default off, rollout NO-GO | Canaries separate Task Pipeline identity from physical Worker Slots; only explicit PRD/Issue/Refactor shadow/blocking profiles may use it |
| Research generation freshness | Deterministic implementation complete, real E2E pending | prompt, config, route, role, Task, and Run Contract freeze one generation; startup isolates stale generations |
| Recovery Coordinator convergence | Candidate, not implemented | Supervisor and Run Manager remain separate components and cannot be operated as one current endpoint |
| OpenCode Provider SPI | Candidate, not implemented | Current public Provider paths must not claim an OpenCode native session |

The current topology/profile can compose the following execution shapes as a
DAG. They are not a fixed Reader -> Writer -> Verifier pipeline that every
Workflow must traverse:

```mermaid
flowchart TB
  READY["Kernel Ready Set<br/>admitted topology + dependency + WIP"] --> SHAPE{"Registered Stage Shape"}

  SHAPE --> SINGLE["Single Role Stage"]
  SINGLE --> ONE["One Contracted Worker Attempt"]

  SHAPE --> RF["Reader Fanout"]
  RF --> R1["Read-only Child A"]
  RF --> R2["Read-only Child B"]
  RF --> RN["Read-only Child N"]
  R1 --> RS["wait_for_all / Synth Contract"]
  R2 --> RS
  RN --> RS

  SHAPE --> WF["Scoped Writer Fanout"]
  WF --> W1["Task A<br/>scope + isolated worktree"]
  WF --> W2["Task B<br/>scope + isolated worktree"]
  WF --> WN["Task N<br/>scope + isolated worktree"]
  W1 --> CANDIDATE["Candidate Assembly<br/>scope / conflict / currentness admission"]
  W2 --> CANDIDATE
  WN --> CANDIDATE

  SHAPE --> VF["Verifier / Judge Fanout"]
  VF --> V1["Tests on Exact Target"]
  VF --> V2["Coverage / Parity Evidence"]
  VF --> VN["Independent Thin Judge"]
  V1 --> VA["Evidence Aggregate"]
  V2 --> VA
  VN --> VA

  ONE --> OUTCOME["Typed Outcome + Artifact Refs"]
  RS --> OUTCOME
  CANDIDATE --> OUTCOME
  VA --> OUTCOME
  OUTCOME --> REDUCER["Kernel Reducer / Barrier / Gate"]
  REDUCER -- "continue" --> NEXT["Next Ready Operation<br/>returns to Kernel Ready Set"]
  REDUCER -- "semantic gap" --> REPLAN["Bounded Rework / Replan<br/>returns as new admitted generation"]
  REDUCER -- "Goal Claims satisfied" --> COMPLETE["Completion Gate / Delivery"]
  REDUCER -- "bounded terminal failure" --> BLOCKED["Owner-visible Blocker"]
```

One profile may feed a Reader aggregate into the next Stage or let a Candidate
trigger exact-target verification. That ordering comes from the admitted DAG,
not from a hard-coded global barrier in this diagram. v3 Writer fanout by itself
does not imply persistent Task Pipeline identity. Only an explicit v4 profile
enables the Task-local pipeline and reusable Worker Slots below:

```text
Task A: Impl -> Task Verify -> Integration Admission -> serial Candidate Integration
              | failed -> bounded Task A rework
              | passed -> Impl slot may serve Task C immediately

all admitted Task receipts
  -> freeze exact Candidate
  -> Global Candidate Verify / Discovery / Goal Closure
```

The v4 `verify_admitted` default is a zero-Agent-turn mechanical admission.
High-risk `risk_review` is a separate, default-off canary. Local Task success
never replaces global acceptance of the frozen exact Candidate, and a partial
Candidate cannot auto-ship. Available examples live under
`examples/prod/controller/*-task-pipeline-v4-canary*.yaml` and declare
`preferred: false`.

### Controlled Dynamic Workflows

Dynamic does not mean arbitrary live graph mutation. The releasable path is:

```text
Requirement
  -> Agent/Skill synthesizes a typed FlowSpec
  -> graph/config diff + preflight
  -> exact proposal approval
  -> frozen effective config + Run Contract
  -> Kernel executes registered operations and typed dependencies
```

Changes to an active Run enter through controlled replan, a new generation, or
a registered read-only continuation. An Agent cannot hot-edit `zf.yaml`, invent
arbitrary handlers or events, or recruit unbounded recursive Agents. Stable
controllers serve common PRD, Issue, and Refactor flows; static-safe Generic
Workflow serves long-tail compositions.

### Swarm Invariants

- A child cannot directly write TaskStore, EventLog files, or Run terminal state; it submits sanctioned result/evidence.
- Fanout carries parent, Run, generation, and child identity plus aggregation, timeout, and failure contracts.
- Writers use explicit scope and isolated workdirs; the Kernel serializes or rejects shared/exclusive path conflicts.
- A provider-native child is not another ZaoFu Agent or Task and cannot recurse past configured budget.
- Autoscaling changes compatible instances only inside a `zf.yaml` role policy; it never mutates canonical topology.
- Aggregate, Verify, or Judge prose cannot bypass Completion Gate and self-declare Goal completion.

## 7. Current Runtime Path

```text
zf start
  -> load zf.yaml + project.state_dir
  -> reconcile stale Research generations before transport startup
  -> start tmux and/or stream-json transports and sidecars
  -> EventWatcher tails events.jsonl
  -> wake-worthy event calls WorkflowRuntimeCoordinator.run_once()
  -> topology/profile selects mechanical next work
     -> v3: declared stage/fanout/barrier route
     -> v4 blocking canary: Task-local Impl/Verify/Integration operations
  -> briefing + contract + required inputs reach worker
  -> worker emits facts/results/evidence
  -> reducers/gates update sanctioned state
```

Periodic watcher ticks also drive liveness, continuation, projection refresh,
and recovery scans. Starting tmux without the watcher may strand a long-running
Workflow.

## 8. Task, Workflow, And Run

- A Task is a canonical work unit with one `contract` for behavior, scope, acceptance, verification, and owner.
- A Workflow is a registered topology/route, not Kernel control logic invented from chat.
- Task Map connects Goal Claims, Tasks, dependencies, waves, scope, and evidence producers.
- A dynamic Workflow first becomes a typed proposal and executes only after approval freezes it into the Run Contract.
- TaskAttempt persists identity and lease before transport; late results must pass currentness checks.
- Run freezes proposal, effective config, goal, and generation and converges to an exclusive terminal.
- `Task done` is not Goal closure; Closure and Completion Gate re-check mandatory Claims.

## 9. Delivery And Recovery Loops

| Loop | Shape |
|---|---|
| Delivery | intake -> plan -> task map -> implementation -> verification -> Thin Judge -> completion gate -> ship |
| Quality | contract -> typed result -> evidence gate -> pass / negative handoff |
| Recovery | failure/stall -> Supervisor -> Run Manager -> controlled action -> post-verification |
| Harness improvement | repeated fingerprint -> Autoresearch -> isolated proposal -> verify/apply |
| Human approval | Plan hold -> approve/reject -> execute, repair, or stop |

Run continuation selects zero or one current operation per tick. Repeated
no-progress recovery must converge to blocked with evidence instead of remaining
active or retrying without bound.

## 10. Safety And Constraints

- Workers and Agents report facts/intent only through `zf emit`, controlled CLI, artifacts, or controlled actions.
- Integrations and Web do not write canonical business state directly.
- Protected paths, scope, tool closure, budget, nonce/signature, and related controls follow `zf.yaml`.
- Provider CLIs can change code and spend budget; validate, preflight, and review scope before real execution.
- Operator tokens do not enter provider sessions.
- Project-specific acceptance, parity, and semantic gates belong in skills, prompts, and artifacts; the Kernel enforces mechanical boundaries.

Some security controls require explicit configuration. “Supported by code” does
not mean “enabled in every Project.”

## 11. `project.state_dir`

The default `.zf/` is runtime state, not source code:

| Content | Type |
|---|---|
| `events.jsonl` | append-only occurrence ledger |
| `kanban.json`, `feature_list.json` | canonical current stores |
| `task_attempts.json`, session stores | attempt, lease, and session identity |
| `artifacts/`, sidecars, accepted packages | complete semantics and evidence |
| `projections/`, SQLite, Trace/Graph/Loop | rebuildable read models |
| `workdirs/` | isolated worktrees/checkouts |
| `logs/`, transcripts | runtime logs and provider payloads |

Do not edit canonical files by hand. Use Store/helpers, `zf` CLI, or controlled
actions.

## 12. Next

- [From goal to verified delivery](concepts/delivery-control-model.en.md)
- [Harness runtime](04-harness-runtime.en.md)
- [Plan, Task Map, and dispatch](13-plan-task-map-orchestrator-dispatch.en.md)
- [Product fanout real E2E](18-product-fanout-real-e2e.en.md)
- [Context, Artifacts, and Handoff](operations/context-handoff-artifacts.en.md)
- [Observe a delivery](operations/observe-delivery.en.md)
- [Recover a long-running Run](operations/recover-long-running-run.en.md)
