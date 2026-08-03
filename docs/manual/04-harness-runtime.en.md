# Harness Runtime Flow

> For operators who need to understand how ZaoFu advances an approved goal into tasks, execution, evidence, and closure.
> The current authority boundary follows verified code, tests, and the Product Flow / Legacy safe-team contracts documented here.

## 1. Separate Fact Authority from Orchestration Mode

ZaoFu is not a single-layer system where one Agent commands all other Agents. Runtime facts have distinct owners:

| Layer | Responsibility | Sanctioned writer |
|---|---|---|
| control plane | topology, roles, policies, budgets, `project.state_dir` | `zf.yaml` |
| occurrence ledger | ordering, causation, verdicts, and references | `EventWriter` / `EventLog` |
| current state | Task, Feature, Session, RoleSession, TaskAttempt | the matching Store and Kernel action |
| semantics and evidence | plans, Task Maps, matrices, reports, large payloads | atomic artifact/sidecar writer plus ref/digest |
| query projections | SQLite, Web, Trace, Graph, Loop, summaries | projector/read model; diagnosable and rebuildable |

Workers report facts, evidence, or action intent. They do not mutate Kernel-managed canonical state.
Web, Channel, Feishu, and Kanban Agent follow the same boundary.

ZaoFu supports two explicitly selected orchestration modes:

| Mode | Happy-path owner | `orchestrator` role responsibility |
|---|---|---|
| Product Flow | Kernel mechanical dispatch from topology/profile | low-frequency exception triage and replan/proposal; no direct state writes |
| Legacy safe-team | Layer 2 Agent may perform semantic decomposition, contract synthesis, and explicit assignment | compatibility mode selected by profile |

Treat new Issue, PRD, Refactor, and long-horizon flows as Product Flow. Do not generalize the
Layer 2 behavior of legacy safe-team, and do not conflate the Python `Orchestrator` with a configured
`orchestrator` role Agent.

## 2. Product Flow Path

```text
approved request / typed event / artifact
  -> run admission
  -> Kernel resolves workflow topology and readiness
  -> persist TaskAttempt / dispatch token
  -> deliver briefing through the transport
  -> Worker emits result, artifacts, and evidence
  -> Kernel applies schema, state, evidence-presence, and safety gates
  -> next stage / fanout / barrier / bounded rework
  -> terminal predicate + closure verdict
```

Three boundaries matter:

1. Agents, skills, and prompts own project semantics, decomposition methods, acceptance quality, and solution judgment.
2. The Kernel owns declared-topology readiness, WIP, leases, dispatch, mechanical gates, and transitions.
3. Semantic exception handling first produces a proposal; only `ControlledActionService` applies an approved action.

![Animated loop from Project to verified delivery and controlled recovery](assets/concept-delivery-control-loop.webp)

The effective stages, pipelines, fanout, terminal predicates, and rework routes come from `zf.yaml`
and `zf workflow inspect`, not from a hard-coded dev-review-test-judge chain in a manual.

## 3. Start, Watcher, and Wakeups

`zf start` loads `zf.yaml` and the resolved `project.state_dir`, starts configured tmux/stream-json
transports and sidecars, then tails `events.jsonl` through `EventWatcher`:

- wake-worthy events invoke `Orchestrator.run_once()`;
- periodic ticks inspect stalled workers, orphan tasks, context pressure, and recovery requests;
- projections and sidecars refresh under their contracts without becoming a second control plane.

```bash
uv run zf start
uv run zf events --last 30
uv run zf watch --follow
uv run zf status --workers
```

`--foreground` is a compatibility no-op alias. `--no-watch` explicitly disables the long-running
watcher. Starting tmux without the watcher can leave returned Worker results unconsumed.

## 4. Admission, Attempt, and Dispatch Token

Before dispatch, the Kernel checks at least:

- the run is admitted and task dependencies are ready;
- stage, role, and worker resolve from the declared topology;
- WIP, budget, concurrency, workspace, and security policy allow the attempt;
- Task contract, required artifacts, and input references are complete;
- TaskAttempt/lease is persisted and the dispatch is current.

Each delivery has a distinct `dispatch_id`. Worker results must match the current attempt/token, so a
stale session, replayed event, or duplicate callback cannot close the task. Transport delivery, Worker
result, gate verdict, and task closure are separate lifecycle points.

## 5. Evidence Gates and Completion

An Agent saying “done” is not a terminal condition. Closure requires at least:

- a legal stage/attempt event chain;
- the current topology's terminal predicate;
- resolvable required artifacts, test results, Git evidence, and ref/digest bindings;
- passing mechanical gates and configured discriminators;
- explainable Goal/Claim/Task/Evidence coverage with no unresolved blocker.

`quality_gates` check commands or mechanical facts. Discriminators check contract evidence. Semantic
acceptance methods and product parity belong in skills, prompts, and Agent artifacts rather than
project-specific runtime hard-coding.

`zf kanban move <task_id> done` also passes through the active topology's closure checks. Missing
evidence causes a rejected transition and an auditable event.

## 6. Fanout, Barriers, and Bounded Rework

Product Flow can declare sequential stages, fanout/fan-in, lanes, barriers, reader/writer roles, and
custom Issue/PRD/Refactor topologies. The Kernel schedules only the declared mechanical dependencies.

Rework destinations are resolved from the current contract and topology, typically in this order:

1. a legal rework instruction on the Task contract;
2. `workflow.rework_routing`;
3. a compatibility default from the profile.

`max_rework_attempts`, no-progress detection, and budget gates prevent unbounded loops. Crossing a
boundary produces an owner-visible escalation, replan proposal, or controlled recovery instead of
silently replaying the same prompt.

## 7. Long-Running Recovery and Context Inheritance

| Risk | Runtime signal | Response |
|---|---|---|
| Worker makes no progress | stuck/silent-stall/no-progress | checkpoint, retry, requeue, or semantic triage |
| Task runs without a valid result | orphan/lease expiry | validate the attempt, then recover or escalate |
| Context approaches its limit | warning/compact/hard-cap | write artifacts/StatePacket, then compact or rotate session |
| Plan no longer fits the goal | goal gap/replan required | produce a proposal and apply it only after approval |

Common role settings include:

```yaml
stuck_threshold_seconds: 180
orphan_warning_seconds: 300
orphan_escalate_seconds: 600
context_window_tokens: 200000
context_warning_threshold: ${ZF_CONTEXT_WARNING_THRESHOLD:-0.6}
context_compact_threshold: ${ZF_CONTEXT_COMPACT_THRESHOLD:-0.7}
context_hard_cap: ${ZF_CONTEXT_HARD_CAP:-0.9}
max_rework_attempts: 3
```

`.env` only supplies variables that `zf.yaml` actually references. Validate legacy aliases through the
config loader and `zf validate --cold-start`.

See [Recover a Long-Running Run](operations/recover-long-running-run.en.md) and
[Context, Handoff, and Artifacts](operations/context-handoff-artifacts.en.md).

## 8. Observability, Supervisor, and Autoresearch

- Provider transcript/session tailers convert tool calls, text, and usage into `agent.*` events or sidecar refs.
- Run Manager manages retryable and resumable run/attempt semantics; a resident Agent can only advise.
- Supervisor observes failure signals and creates owner-visible decisions; it does not kill Workers or hand-edit state.
- Autoresearch performs deep diagnosis or isolated repair candidates and does not apply directly to mainline by default.

Codex/Claude hooks improve telemetry but do not own Task truth. Missing hook authorization creates an
observability gap; it proves neither execution failure nor completion.

## 9. Delivery Sign-off

```bash
uv run zf kanban --board
uv run zf task trace <task_id>
uv run zf refs verify
uv run zf metrics snapshot
uv run zf doctor
```

Confirm that the Task/Feature closed through its terminal predicate; Goal Dossier claim coverage and
evidence can be read back; no fatal/blocker remains; Git base/head/log/diff is identifiable; and required
tests, projection freshness, and external effects have evidence. See [Observe a Delivery](operations/observe-delivery.en.md).
