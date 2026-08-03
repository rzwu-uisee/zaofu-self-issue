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
| Is every Goal Claim covered? | `Delivery -> Delivery Map -> Coverage` |
| How do Goal, Claims, and canonical Tasks relate? | `Delivery -> Delivery Map -> Work` |
| What runtime/gate/artifact detail explains a problem? | `Delivery -> Delivery Map -> Diagnostics` |
| What happened in exact causal order? | `Monitoring -> Observability -> Traces/Events` |
| What is the human-readable package for one Run? | `Monitoring -> Observability -> Runs -> Goal Dossier` |
| What needs a human decision or attention? | `Inbox` |

The `playgroud` animation follows one diagnostic route through Overview, Runs,
Coverage, Work, Diagnostics, Loop, and Observability. Every surface reads the
same Feature, Run, and Event chain.

![Observe one playgroud delivery through Overview, Runs, Graph, Loop, and Observability](../assets/observe-delivery.webp)

## Delivery Overview

Overview first answers:

- current verdict and ship readiness;
- current phase or cycle;
- blocker, why-not-done, and next owner;
- total Tasks, completion, cost, and duration;
- drift, rework, or recovery signals.

It is a navigation summary. It does not re-decide Task, Run, or Closure state.

## Runs And Spans

`Runs` presents execution by Run:

- stage and role;
- attempt, dispatch identity, and retry;
- queued, assigned, running, and terminal lifecycle;
- fanout/fanin and dependency barriers;
- gates, results, duration, and causation.

`Spans` locates event order and call causation. When a Run is slow or appears to
skip a step, inspect Runs first and then drill into Spans/Trace. Do not infer the
cause from one status badge.

## Coverage, Work, Diagnostics

The three Graph views answer different questions:

| View | Main audience | It explains... |
|---|---|---|
| Coverage | PM, Owner, Reviewer | Plan, Implementation, Verification, Closure, and Gap for each Claim |
| Work | Engineer, Delivery Owner | Goal -> Claim -> Task, plus Task Try, Result, and Evidence |
| Diagnostics | Operator, harness maintainer | runtime, gate, behavior, evaluation, and artifact relationships |

A done Task with an open Claim usually means:

- the Task did not declare coverage for that Claim;
- verification is missing, failed, or belongs to an old generation;
- evidence does not match target or contract identity;
- Goal Closure still has an open gap.

Work keeps one primary node per canonical Task. Secondary Claim coverage is a
relation, not a duplicate Task state.

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
