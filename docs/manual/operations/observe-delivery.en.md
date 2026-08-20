# Observe A Delivery

[中文](observe-delivery.md) · [Operations index](README.en.md)

> Goal: answer “what is the goal, where is it now, why is it not done, what is
> the evidence, and who acts next” without reading every provider transcript.
> These surfaces are read-only projections and do not own dispatch. Read-only
> means they do not change canonical Task, Run, or Event truth; a query may
> incrementally refresh rebuildable SQLite or JSON projections.

## Choose The Right Object First

| Question | Open first |
|---|---|
| What work exists and who owns it? | `Tasks` |
| Is the overall goal deliverable? | `Delivery` |
| How did stages, attempts, and retries advance? | `Delivery -> Runs` |
| Is every Goal Claim covered by a Task with current evidence? | `Delivery -> Graph` |
| What are a Task's tries, gates, results, and evidence? | `Delivery -> Runs -> Inspector` or the canonical Task |
| What happened in exact causal order? | `Traces` |
| How do behaviors, evaluations, and improvements converge? | `Loop` |
| What is the human-readable package for one Run? | `Monitoring -> Observability -> Runs -> Goal Dossier` |
| What needs a human decision or attention? | `Inbox` |

The `playgroud` animation is a historical UI example. For current operation,
follow `Overview -> Runs/Graph -> Task/Traces/Loop`. Every surface still reads
the same Feature, Run, and Event chain.

![Observe one playgroud delivery through Overview, Runs, Graph, Loop, and Observability](../assets/observe-delivery.webp)

## Delivery Overview

Overview first answers:

- current verdict and ship readiness;
- current phase or cycle;
- blocker, why-not-done, and next owner;
- total Tasks, completion, cost, and duration;
- drift, rework, or recovery signals.

It is a navigation summary. It does not re-decide Task, Run, or Closure state.

## Runs And Inspector

`Runs` presents execution by Run:

- stage and role;
- attempt, dispatch identity, and retry;
- queued, assigned, running, and terminal lifecycle;
- fanout/fanin and dependency barriers;
- gates, results, duration, and causation.

Runs has one Run workbench. Select a Task to inspect tries, gates, events,
evidence, and regression capture/replay. Use `Traces` for temporal order and a
Span Tree/Waterfall. Runs shows that handoff only when the server returns a
verified canonical Trace reference.

## Graph

Graph opens on a lightweight Goal -> Claim -> canonical Task Coverage surface.
Switch to Work when you need execution detail: it first reuses the loaded Goal
summaries. Select a Goal and ZaoFu immediately loads that
Goal's Work tree, ownership, current execution state, attempts, gates, and evidence.
The Diagnostics lens is removed. In Coverage, each Claim shows:

- whether Plan has a covering Task;
- Implementation and Verification results;
- Closure, open Gaps, and generation/currentness;
- a link to the covering canonical Task.

A done Task with an open Claim usually means:

- the Task did not declare coverage for that Claim;
- verification is missing, failed, or belongs to an old generation;
- evidence does not match target or contract identity;
- Goal Closure still has an open gap.

Drill into the covering Task or Runs for detailed evidence, use a verified Trace
handoff for temporal diagnosis, and use Loop for behavior/evaluation/improvement.
Work does not request detail or its graph chunk before Goal selection. Graph does not duplicate
Thick Graph, Trace/Span, or Loop diagnostic surfaces.

## Goal Dossier

Goal Dossier is a run-scoped, rebuildable human-readable projection containing:

- Goal objective and terminal status;
- roadmap and Tasks;
- mandatory Claim coverage;
- evidence index;
- active and resolved gaps;
- closure, delivery readiness, and source freshness;
- source fingerprint and refs.

It explains a Run; it does not decide a Run. When Dossier, terminal truth, and
receipt disagree, ZaoFu may suppress an incorrect owner completion message, but
the Dossier cannot rewrite the canonical terminal.

Generate the same projection from CLI:

```bash
zf report goal-dossier --run-id RUN-ID --out /tmp/goal-dossier
```

This writes the requested output directory and refreshes the rebuildable Goal
Dossier/SQLite projection under the state directory. It cannot rewrite the
canonical terminal.

## Inbox

Inbox groups human-facing work:

- Plan and Workflow approval;
- human decisions or waivers;
- runtime attention and recovery decisions;
- Run delivery and owner receipts;
- integration or automation notifications.

An Inbox action uses a token-gated controlled action and records audit facts.
Marking an item read does not mutate Task or Run business state.

## CLI Reconciliation

```bash
zf kanban --board
zf task trace TASK-ID
zf trace delivery FEATURE-ID
zf events --last 100
zf projection status --json
zf projection doctor --projection all --json
```

When a projection is stale or degraded, diagnose the projection first. An empty
page is not a reason to edit a canonical store. `status` and `doctor` may
initialize the SQLite schema or refresh projection metadata; only explicit
`repair` or `rebuild` may quarantine or fully rebuild the affected read model.

## Acceptance Questions

Before closing a delivery, answer:

1. What are the original Goal and mandatory Claims?
2. Which Tasks cover each Claim?
3. Which run, generation, contract, and target own the current result/evidence?
4. Which Gaps remain, and who owns the next action?
5. Why did the Completion Gate pass or block?
6. Does the owner-visible result agree with Goal Dossier?

If these are not answerable, the state is not yet an explainable delivery.

## Related

- [From goal to verified delivery](../concepts/delivery-control-model.en.md)
- [Detailed Delivery Trace reference](../14-delivery-trace-usage.en.md)
- [Recover a long-running Run](recover-long-running-run.en.md)
