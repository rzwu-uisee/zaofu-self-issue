# From Goal To Verified Delivery

[中文](delivery-control-model.md) · [Concept index](README.en.md)

## What ZaoFu Solves

Coding agents are already strong at reading and modifying code inside one
context. ZaoFu adds team-scale delivery control: goals remain stable across
sessions, work is assignable, results are verifiable, failures are recoverable,
and people can see state and decide before high-risk actions.

ZaoFu does not replace provider agents. Codex, Claude Code, and other supported
CLIs keep code judgment and tool execution. ZaoFu supplies deterministic runtime
boundaries, contracts, handoffs, evidence, recovery, and operator surfaces.

## Six Product Objects

| Object | It answers... | It does not... |
|---|---|---|
| Project | Which repository, control plane, and runtime state own this work? | Automatically create a Task or Run |
| Channel | Has the request and its decisions been clarified by people and agents? | Schedule code delivery |
| Task | Which contracted work unit is owned by whom? | Prove the Goal is complete by itself |
| Workflow | Which approved stages and gates should a Task traverse? | Accept arbitrary agent-generated control logic |
| Run | What is the identity, attempt state, and terminal outcome of this approved execution? | Let a Web projection decide state |
| Delivery | Do Goal, Claims, Tasks, evidence, Gaps, and Closure agree? | Create another source of truth |

## One UI Handoff

The animation uses an isolated `playgroud` Project. It moves through Project
admission, Project status, Task contracts, the Delivery cockpit, the Goal/Task
Work graph, and the Recovery Loop. These are not six independent states: every
view reads the same Project/Run identity, canonical stores, event ledger, and
query projections.

![Animated playgroud journey from Project creation to Delivery and the Recovery Loop](../assets/concept-delivery-control-loop.webp)

## Goal To Closure

```text
Requirement
  -> Goal: the objective for this Run
  -> Goal Claims: immutable delivery statements that must each be explained
  -> Task Map: Tasks, Claim coverage, dependencies, and owners
  -> TaskContract: behavior, scope, verification, and non-goals for an attempt
  -> Result + Evidence: implementation and independent-verification outputs
  -> Gap: uncovered, failed, stale, or identity-mismatched facts
  -> Goal Closure: Claim-level conclusions
  -> Completion Gate: mechanical consistency checks
  -> Goal Dossier + owner receipt
```

`Task done` does not mean `Goal closed`. A Task may not cover a mandatory Claim,
a verification result may belong to an old generation, or evidence may not match
the target revision.

## Layered Runtime Authority

| Layer | Carrier | Authority |
|---|---|---|
| Control plane | `zf.yaml` | topology, roles, policy, budget, safety, and state dir |
| Occurrence ledger | `events.jsonl` | occurrence, ordering, causation, verdict, and refs |
| Current state | Task/Feature/Session/RoleSession/TaskAttempt stores | current operational state and leases |
| Complete semantics | artifacts, sidecars, accepted packages | plans, task maps, results, evidence, and large payloads |
| Query projections | SQLite, Trace, Graph, Loop, Web summaries | search, aggregation, visualization, and freshness |
| Active transport | SSE, LiveDeltaBus, provider streams | ephemeral deltas that do not decide recovery |

Event replay does not mean every canonical store can currently be rebuilt from
`events.jsonl` alone. A projection is disposable and rebuildable; a required
artifact or canonical store may not be.

## Kernel And Agent Boundary

- Kernel/Orchestrator runtime owns identity, admission, mechanical dispatch, schemas, gates, replay, transitions, and external effects.
- Agents and skills own requirement understanding, planning, implementation, review, diagnosis, and product judgment.
- Agents report facts and intent through artifacts, events, or controlled-action proposals; they do not write canonical state directly.
- Web, CLI, and Feishu are read and controlled-action surfaces, not state machines.

Two explicit orchestration modes are supported:

| Mode | Happy-path owner | Scope |
|---|---|---|
| Product Flow | The Kernel dispatches mechanically from the approved topology/profile; Layer 2 handles exceptional triage and proposals | PRD, Issue, Refactor, Research, and long-horizon delivery |
| Legacy safe-team | An explicitly configured Layer 2 Agent may decompose, synthesize contracts, and assign | compatibility, teaching, or explicitly manual profiles |

Documentation and extensions must not flatten both into one global model.

## Five Loops

1. Delivery Loop: intake -> plan -> task map -> implementation -> verification -> closure -> delivery.
2. Quality Loop: contract -> result -> evidence gate -> pass or negative handoff.
3. Recovery Loop: failure/stall -> Supervisor -> Run Manager -> controlled action -> post-verification.
4. Harness Improvement Loop: repeated fingerprint -> Autoresearch -> isolated proposal -> verify/apply.
5. Human Approval Loop: Plan hold -> approve/reject -> execute, repair, or stop.

These loops share identities, events, and artifact refs. They do not share one
mutable agent state machine.

## Next

- [First verified delivery](../getting-started/first-verified-delivery.en.md)
- [Observe a delivery](../operations/observe-delivery.en.md)
- [Recover a long-running Run](../operations/recover-long-running-run.en.md)
- [Architecture overview](../architecture.en.md)
