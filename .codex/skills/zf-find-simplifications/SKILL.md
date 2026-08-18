---
name: zf-find-simplifications
description: "Use on demand to find evidence-backed simplifications in ZaoFu itself, in applications developed with ZaoFu, or in an Autoresearch simplify audit. Proves consumers and protected seams before proposing deletion, coalescing, demotion, or dependency replacement; output is proposal-only until separately approved."
stages: [impl, fix, discovery, scan, reflection]
tags: [simplification, architecture, autoresearch, maintenance]
dependencies: []
auto_inject: false
load_on_demand: true
---

# ZaoFu Find Simplifications

Use this skill to reduce real maintenance surface, not to reward a smaller diff
or a lower line count. A valid candidate has call-site evidence, states what
behavior is surrendered, respects the repository's protected seams, and deletes
or coalesces more complexity than it relocates.

## Operating Modes

Choose exactly one mode before working:

- **Audit mode:** use for broad repository surveys, architecture reduction,
  release/refactor checkpoints, and every Autoresearch `simplify` request. It
  is read-only and produces a proposal; it never authorizes implementation,
  runtime-state mutation, task completion, or direct mainline application.
- **Recent-diff refinement mode:** use only when the current assignment already
  authorizes code edits. Limit edits to the current session's owned diff, keep
  observable behavior exact, and rerun the same focused verification. This is
  the daily ZaoFu/application-development counterpart to a code-simplifier
  pass, not permission to refactor untouched modules.

If scope or edit authorization is ambiguous, use audit mode.

## When To Use

Use it on demand in any of these contexts:

- **ZaoFu daily iteration:** repeated fallback branches, compatibility layers,
  duplicate facts, oversized ownership surfaces, or code repeatedly repaired in
  the same area. After an implementation, recent-diff refinement may improve
  clarity without changing behavior.
- **Applications developed with ZaoFu:** hand-written infrastructure, abandoned
  options, duplicated domain representations, or test-only public APIs in the
  target application. The application's owner contract and product behavior
  take precedence over generic ZaoFu preferences.
- **Autoresearch:** an explicit `autoresearch.loop.requested(mode=simplify)` or
  a separately approved simplification checkpoint. The output remains a
  `simplification_audit.v1` proposal; Autoresearch must not apply it directly.
- **Release/refactor checkpoint:** after the behavior is proven and before more
  variants are added, when consolidation can be evaluated against a stable
  target.

Do not auto-load this skill for every Impl, Verify, reflection, or ordinary code
review. Do not use it to turn an unrelated delivery task into a refactor.

## Recent-Diff Refinement

This narrow mode adopts the useful behavior-preserving discipline of Anthropic's
`code-simplifier` agent while keeping ZaoFu's evidence and ownership rules:

1. Pin the owned scope with `git diff -- <explicit paths>` and the current task
   contract. Do not edit another driver's staged, dirty, or untracked files.
2. Read the repository's current `AGENTS.md` / `CLAUDE.md` and existing nearby
   patterns. Project rules replace hard-coded language/framework preferences.
3. Preserve public APIs, outputs, errors, side effects, ordering, persistence,
   events, config defaults, security boundaries, timing/concurrency semantics,
   and accepted compatibility behavior.
4. Reduce unnecessary nesting, duplicate branches, redundant abstractions,
   vague names, and comments that only narrate syntax. Consolidate only logic
   with one owner and one invariant.
5. Prefer readable, explicit control flow over dense expressions, clever
   one-liners, nested ternaries, or line-count reduction. Fewer lines are not a
   success metric.
6. Apply the smallest refinement, inspect the resulting diff, and rerun the
   pre-refinement focused tests plus any direct caller/contract checks crossed.
7. If a candidate changes supported behavior, requires migration, crosses the
   task's allowed paths, or cannot be proved equivalent, do not apply it. Move
   it to an audit proposal or reject it.

No edit is a valid outcome when the current diff is already the clearest safe
implementation.

## Scope First

State the repository, target commit, bounded directories or subsystem, and the
reason simplification is being considered. Read the applicable `AGENTS.md`,
current owner/design documents, dependency manifest, and recent incident or
change evidence before searching for candidates.

For ZaoFu itself, load `zf-cr` and
`zf-harness-design-impl-game-review` when the audit needs architecture-wide
adjudication, verify claims against current code/tests, and preserve these
boundaries unless the owner explicitly approves an architecture change:

- `zf.yaml` remains the single control-plane configuration.
- EventLog ordering/causation, canonical Stores, artifact bodies/refs/digests,
  and SQLite projections have different owners; do not collapse truth into a
  read model or duplicate semantic bodies into events.
- The deterministic Kernel and a configured `orchestrator` role agent are not
  interchangeable.
- Product Flow and supported Legacy routes may intentionally differ.
- Provider, transport, worktree, session, and security seams may have dynamic
  consumers that a static symbol search cannot see.
- `skills/` is canonical; tracked provider copies are synchronized mirrors, not
  independent implementations.

For an application, derive the protected seams from its PRD, public API,
persisted data, deployment/runtime contract, and real E2E behavior. Never infer
that a product feature is disposable merely because ZaoFu does not use it.

## Survey Method

1. Start with the largest recent production deltas and repeatedly changed
   modules. Use `git log`, `git diff --stat`, line counts, incident reports, and
   rework evidence to prioritize the survey.
2. Search exact symbols, event names, config keys, routes, wire strings, artifact
   kinds, CLI commands, and reflective registry names with `rg`.
3. Read every plausible caller. Classify consumers as:
   - `production_static`: direct runtime callers;
   - `production_dynamic`: registries, config, events, plugins, serialization,
     templates, provider or transport loading;
   - `operations`: migration, recovery, CLI, runbook, compatibility;
   - `non_production`: tests, fixtures, docs, examples, snapshots;
   - `unresolved`: evidence is insufficient.
4. Draw the fact/ownership or lifecycle graph when two mechanisms appear to
   represent the same state. Similar names are not proof of duplication.
5. Check existing design decisions and prior incidents. A defensive branch that
   looks redundant may encode a recovered production failure.
6. Estimate **net surface delta**: removed implementation, tests, config, docs,
   events, and maintenance paths minus replacement glue, migration, dependency,
   compatibility, and new tests.
7. Reject weak candidates before writing the report. Prefer three proven items
   over twenty guesses.

## Strong Candidates

- A public method, event, config option, registry notification, or artifact has
  no production, dynamic, operational, or compatibility consumer.
- Two writable representations claim authority over the same fact and one can
  become a projection or derived view.
- Every adapter implements a method that no caller uses.
- A compatibility path has a proved expiration condition and no surviving
  persisted/wire contract.
- A local implementation can be replaced by a standard-library primitive or a
  healthy dependency with a meaningful net deletion. In ZaoFu, standard library
  remains the default; a dependency needs explicit footprint and supply-chain
  justification.
- Tests or docs are the only consumers and they preserve speculative behavior
  rather than a supported contract.

Reject or defer when a dynamic caller is unresolved, the change is primarily a
product decision, migration/compatibility survives, replacement glue carries
the same complexity, or the proposal merely moves semantics from code into an
opaque prompt.

## Output Contract

Produce one `simplification_audit.v1` body with these sections:

```yaml
schema_version: simplification_audit.v1
mode: audit
target:
  repository: <path/name>
  commit: <sha>
  scope: [<bounded area>]
surveyed_areas: []
protected_seams: []
simplification_candidates:
  - id: SIM-001
    current_surface: []
    consumer_evidence: []
    proposal: <remove/coalesce/demote/replace>
    net_surface_delta: <concrete estimate>
    behavior_given_up: <explicit or none>
    risks: []
    verification_plan: []
rejected_candidates:
  - candidate: <idea>
    reason: <caller/seam/insufficient net deletion>
unknowns: []
apply_policy: proposal_only
```

Every accepted candidate needs file/line or runtime evidence, a strongest
counterargument, and focused verification. Missing evidence moves it to
`unknowns` or `rejected_candidates`; it does not become a confident finding.

When an active ZaoFu task requires a durable artifact, write the complete body
to the task-provided path and publish it through the sanctioned artifact
manifest command, for example:

```bash
uv run zf artifact manifest create \
  --task "$TASK_ID" --role "$ROLE" \
  --kind simplification_audit=/absolute/path/to/audit.yaml \
  --skill zf-find-simplifications --emit
```

Without an active task, return the report to the operator or place an approved
candidate under `backlogs/`; do not emit workflow events merely to record it.

## Apply Boundary

Audit-mode candidates require a separate owner decision before implementation.
Recent-diff refinement needs no second proposal when the current task already
authorizes edits, but it remains inside owned paths and behavior-preserving
verification. Reproduce against current HEAD before any later audit candidate
is implemented. Autoresearch may convert a confirmed bug-class finding into the
existing self-repair route, but a broad cleanup proposal is never a self-repair
authorization.

How to test trigger precision:

- `使用 zf-find-simplifications 审计最近反复修复的 runtime 模块，只输出提案。`
- `对这个由 ZaoFu 开发的 app 做简化审计，保留 PRD 行为。`
- `精简我本轮刚修改的代码，保持行为不变并重跑原测试。`
- submit a mock `mode=simplify` loop request and verify that resident selects
  `simplification-audit` while the completed envelope remains proposal-only.
