# Self-Evolution and Capability Accumulation

[中文](23-self-evolution-learning.md) · [Autoresearch](10-autoresearch-usage.en.md) · [Metrics, Observability, and Operations](21-metrics-observability-operations.en.md)

> Status: `partial` / explicit opt-in. This manual describes the implemented, evidence-bound capability-accumulation loop. It is not model training and does not automatically edit source, `zf.yaml`, Skills, or the main branch.

## 1. What Self-Evolution Means in ZaoFu

ZaoFu does not let an Agent rewrite the system after one answer. It turns verified run experience into traceable, evaluable, revocable capability assets that can be reused only when they apply to a later task.

```text
Verified Autoresearch learn Run
  -> typed capability deposition in its Run Archive
  -> Run Manager policy and integrity checks
  -> immutable campaign and evolution attempt
  -> isolated repeated baseline / candidate trials
  -> independent sealed-evaluator comparison
  -> learning-asset candidate
  -> controlled transition / independent canary
  -> active_retained or revoked
  -> scoped injection into later Briefings
```

It closes the loop from a discovered problem to a reusable capability:

- retain low-risk failure patterns, runbooks, and regression fixtures;
- create evidence-bound proposals, rather than automatic changes, for Skills, Workflows, and Provider routes;
- require baseline/candidate trials, replication, and an independent canary so one lucky result is not treated as improvement;
- record usage outcomes and negative transfer, and revoke an asset when warranted.

Product Workflow Task, Run, Gate, and Delivery truth remains owned by the Kernel and canonical stores. Evolution work runs after a product Run is terminal; it cannot reopen that Run or rewrite its Task truth.

## 2. Loop Ownership and Boundaries

| Step | Current owner | Responsibility | Not responsible for |
|---|---|---|---|
| Learn input | Autoresearch Run Archive | verified evidence and one typed deposition | direct adoption or capability mutation |
| Campaign / attempt | Run Manager + Evolution Coordinator | identity, policy, budget, trials, lifecycle | arbitrary side effects |
| Provider trial | Autoresearch resident | isolated baseline, candidate, and canary execution | adoption decisions |
| Evaluation | sealed evaluator authority | generation, sealed cases, gates, scores, comparability | product Task state |
| Adoption | ControlledActionService / object-specific apply owner | receipt-bound transition or apply | bypassing owner approval |
| Reuse | CapabilityRegistry + briefing projection | scoped read-only asset injection | Agent canonical-state writes |

Automatic reconciliation consumes only a verified `autoresearch.loop.completed` event in `learn` mode with a Run Archive containing exactly one typed deposition. Ordinary Event text, Web projections, and unarchived conversation cannot become learning assets.

## 3. Prerequisites and Minimal Configuration

`runtime.evolution.enabled` defaults to `false`. Start with `evaluate_only`: it builds evidence, runs comparisons, and produces proposals, but does not automatically retain an asset.

```yaml
runtime:
  autoresearch_resident:
    enabled: true
    interval_seconds: 10
    max_actions_per_tick: 1
    worktree_root: /tmp/zaofu-autoresearch-resident/worktrees

  evolution:
    enabled: true
    mode: evaluate_only
    backend: codex
    trial_repetitions: 2
    trial_timeout_seconds: 300
    lease_seconds: 600
    max_trial_attempts: 2
    max_actions_per_tick: 4
    max_cost_usd: 2.0
    max_tokens: 50000
    sealed_root: .zf/evolution/sealed
    access_token_env: ZF_EVOLUTION_EVALUATOR_TOKEN
```

- `runtime.autoresearch_resident.enabled` must be `true` or evolution fails closed;
- `backend` currently accepts only `codex` and `claude-code`;
- `sealed_root` is required and must not be exposed to candidate Agents, Web, or ordinary artifact browsing;
- `access_token_env` is an environment-variable name, never a token value;
- only `mode: auto_low_risk` may automatically progress the `memory_entry`, `runbook`, and `regression_fixture` allowlist;
- `skill_prompt`, `framework_code`, `workflow_config`, `provider_route`, and
  `tool_capability` remain proposal-only. A Skill Candidate may first become a scoped
  overlay, but writing canonical source still requires an owner action token and its
  object-specific apply path.
- `skill_prompt` is not in the default `auto_asset_kinds`. It must be explicitly
  allowlisted to advance automatically into a scoped canary. Once active, source
  drift, expiry, budget overrun, or negative transfer may revoke the overlay through
  a Run Manager Controlled Action even when it is no longer allowlisted.

Validate without starting a real Provider:

```bash
uv run zf validate --cold-start
```

Determine the configured `project.state_dir` first. The commands below use `$STATE_DIR` and never assume every project uses `.zf`.

## 4. Observe a Campaign

After starting an enabled runtime, read the projection and event stream:

```bash
STATE_DIR=/path/to/configured-state-dir

uv run zf evolution status --state-dir "$STATE_DIR"
uv run zf watch --type evolution.campaign.materialized --follow --state-dir "$STATE_DIR"
uv run zf watch --type evolution.trial.execution.completed --follow --state-dir "$STATE_DIR"
uv run zf watch --type evolution.canary.completed --follow --state-dir "$STATE_DIR"
```

Assess a campaign in this order:

1. `campaign.materialized` binds source Run Archive, deposition digest, and policy digest.
2. Baseline and candidate complete repeated trials under the same attempt.
3. Evaluator generation, environment fingerprint, TCB, and Archives are verifiable.
4. The comparison is comparable, rather than `incomparable`, timeout, or infrastructure failure.
5. An independent canary has completed, with a final `active_retained` or `revoked` asset state.
6. After reuse, usage outcomes and negative-transfer evidence exist.

Typical durable evidence under the configured state dir:

```text
events.jsonl                         occurrence, causation, and controlled-action facts
evolution/trials.json                attempt and trial lease/settlement current state
evolution/capabilities.json          learning-asset lifecycle and current version
evolution/attempts/                  immutable evolution-attempt sidecars
evolution/campaigns/                 immutable campaign sidecars
evolution/snapshots/                 frozen environment and policy snapshots
runs/                                verifiable trial and canary Run Archives
```

`zf evolution status`, Web, and Graph are read surfaces. Diagnose an adoption conclusion from EventLog, Registry, and immutable sidecars rather than a card color or one score.

## 5. Evaluation, Environment, and Canary Gates

An improvement claim needs all of the following:

- gates precede scores; missing/invalid required gates or score dimensions and blocking-gate failures cannot be offset by a total score;
- baseline and candidate use frozen evaluator generation and comparable inputs; an environment mismatch is `incomparable`;
- sealed evaluator bodies never enter candidate Context; canary uses a different generation;
- trials have leases, idempotency keys, attempt caps, and single-winner settlement so restarts do not duplicate billing or adoption;
- real Provider execution performs environment preflight for Provider, CLI/toolchain, lockfile, sandbox/network, and credential capability snapshots;
- infrastructure failures converge through bounded retry/dead-letter and are not semantic evidence that candidate is worse.

An environment snapshot proves comparable execution dependencies, not product acceptance. Delivery, Task Gates, and terminal evidence remain governed by existing Workflow contracts.

### 5.1 Skill Evaluation, Optimization, and Deactivation

Skills use `raw/current/candidate` arms instead of the generic asset
baseline/candidate pair. Adoption freezes the same model, workspace, prompt, support
Skills, evaluator generation, and routing pool, and requires the policy's distinct
cases and replicates. A Candidate must be loaded through the normal Codex or Claude
Code Skill projection. Inlining its body into the prompt is not a Skill trial.

```text
immutable Skill Candidate
  -> counterbalanced three-arm Provider trials
  -> Run Archive + typed routing/feedback evidence
  -> proposal_only learning asset
  -> canary_active scoped overlay
  -> negative outcome: automatic revoke for future dispatches
  -> passed canary: exact source proposal
  -> owner-token apply + Provider parity sync
  -> active_retained
```

Common read and controlled-action entrypoints:

```bash
uv run zf evolution skill-overlay-resolve \
  --state-dir "$STATE_DIR" --role impl --task-family prd --cohort canary-a

uv run zf evolution skill-source-propose \
  --state-dir "$STATE_DIR" --asset-id <asset-id> --version <version>

ZF_EVOLUTION_OWNER_TOKEN='<owner-token>' \
uv run zf evolution skill-source-apply \
  --state-dir "$STATE_DIR" \
  --proposal-ref-file /path/to/proposal-ref.json \
  --owner-token-file /path/to/supplied-token

uv run zf evolution skill-maintenance-propose \
  --state-dir "$STATE_DIR" --skill <skill-name> \
  --action deactivate --evidence-refs-file /path/to/evidence-refs.json \
  --rationale 'matched outcomes show sustained negative transfer'
```

Self-evolution may automatically revoke a `canary_active` overlay so future
dispatches stop loading the Candidate. Role, task family, and cohort must all match;
the dispatch cohort currently uses the exact Task ID. It does not mutate an already-running Provider
context. Autoresearch may propose `optimize`, `replace`, `merge`, or `deactivate`, but
canonical `skills/` optimization, replacement, deactivation, or deletion requires
owner approval, exact source-currentness checks, and `.codex/skills/` plus
`.claude/skills/` parity sync. There is no valid path for an Agent to autonomously
uninstall a source Skill.

### 5.2 Causal Skill Optimizer

Formal Skill optimization uses `skill-treatment-identity.v2` to freeze the runtime
commit, Provider, model, role/profile, prompt, support Skills, workspace fixture,
tool, sandbox, network, budget, and evaluator generation. The Raw, Current, and
Candidate arms may differ only in target-Skill availability, version, and digest.
Any common-identity drift makes the comparison `incomparable`.

Each Provider case produces two independent evidence paths:

```text
final output -> correctness / product gate
Provider stream -> immutable normalized trajectory -> behavior verdict
```

An answer can therefore pass correctness while behavior remains `false` when the
target Skill was not read. A case without an explicit observable behavior contract
uses `null`. Trajectory bodies remain in sidecars; EventLog stores only refs,
digests, and verdicts.

An optimizer campaign uses disjoint Train, Selection, and Test splits:

- the Optimizer Agent sees only the current Skill, Train evidence, failure clusters,
  and the rejection buffer;
- the Selection evaluator chooses steps and must bind the exact split, generation,
  and case-result refs;
- Test remains sealed for the final design-179 adoption proof, and the best candidate
  never writes `skills/` directly.

The production loop has the Autoresearch resident execute a proposal-only Agent
request, a sealed evaluator publish Selection, and Run Manager verify currentness
before settlement. Recovery and operations entrypoints are:

```bash
uv run zf evolution skill-opt-agent-execute \
  --state-dir "$STATE_DIR" \
  --request-event-id <proposal-request-event-id>

uv run zf evolution skill-opt-selection-submit \
  --state-dir "$STATE_DIR" \
  --selection-request-event-id <selection-request-event-id> \
  --evaluation-file /path/to/sealed-selection-result.json
```

`skill-opt-init`, `skill-opt-prepare`, `skill-opt-settle`, and `skill-opt-export`
remain mechanical debugging and recovery commands. Only a v2 campaign with three
immutable split descriptors can enter the Agent route; v1 campaigns remain
recovery-only. A completed best candidate still proceeds through design 179 Test,
routing, canary, and owner-retain gates.

## 6. Memory: Work Notes versus Learning Assets

There are two kinds of cross-session information:

| Type | Storage | Purpose | Current boundary |
|---|---|---|---|
| Ordinary Memory | `memory/shared.md`, `memory/<role>.md`, and archives | decisions, fixes, and Context notes | `max_days` is metadata; default reads are not a strict per-entry expiry/applicability gate |
| Retained Learning Asset | `evolution/capabilities.json` plus immutable artifact | evaluated/canary-proven reusable experience | injected only when lifecycle, scope, expiry, conflict, and canary checks pass |

An ordinary `memory.note` supports handoff but cannot itself prove that an experience improved later work. A self-evolution asset records evidence, version, applicability, usage, and outcome before entering long-lived reuse.

Retained Learning is filtered by lifecycle (`canary_active` or `active_retained`), expiry, contradiction refs, taint, task family, Provider, model, language, repository, and canary scope. Nonmatching assets keep an excluded reason rather than being silently injected.

`memory_entry` and `runbook` bodies may be injected as read-only prompt Context. Other assets use their controlled object path; a free text snippet cannot become runtime control-plane state.

## 7. Context: Live Injection versus Strict Replay

Every Worker or Orchestrator dispatch dynamically assembles Context:

```text
Task contract / Task Capsule
recent events, progress, and recovery facts
repository guidance and loaded Skills
ordinary Memory
applicable Retained Learning
runtime rules, Provider/session, and controlled-tool boundaries
```

The evolution-attempt contract reserves ref/digest fields for the `briefing`, `context read set`, `skill lock`, `memory snapshot`, `tool policy`, and environment snapshot.

Current automatic campaign materialization still reuses the source deposition ref/digest for `context read set`, `skill lock`, and `memory snapshot`. It does not yet freeze the exact Context, Skill versions, and Memory entries read by every trial. Therefore:

- a learning candidate is traceable to a verified Run Archive and its evaluator/environment;
- retained assets can be safely selected and injected;
- historical campaigns are **not** complete word-for-word prompt/context replay records;
- an environment capability snapshot is independent and preflighted, but is not a prompt/context snapshot.

For a strict replay of a Skill or Memory experiment, retain the briefing, Skill digest, and Memory-read evidence as additional artifacts; do not rely on the campaign view alone.

## 8. Lifecycle, Rollback, and Human Boundaries

```text
candidate -> validated -> approved -> canary_active -> active_retained
                  any state may become revoked for insufficient evidence,
                  negative transfer, conflict, or policy change
```

Evolution Coordinator does not autonomously edit source, `zf.yaml`, `skills/`, or
ordinary Memory. A Skill retain writes canonical source only through an explicit
`skill-source-apply` owner action. Object-specific apply owners must supply immutable
receipts, and transitions use revision/CAS protection.

Rollback:

1. Set `runtime.evolution.enabled: false` in a new runtime generation to stop new campaigns.
2. Set `mode: evaluate_only` to stop automatic adoption.
3. Move an active asset to `revoked` through a receipt-bound controlled transition; never hand-edit `capabilities.json`.
4. Preserve EventLog, trials, comparisons, canaries, and receipts for audit.
5. Do not delete product Tasks, Delivery, Run Archives, or history to make a failed campaign disappear.

`zf evolution asset-transition` and `asset-outcome` are mechanical interfaces for controlled actions and operations tooling, not shortcuts around owner gates.

## 9. Recommended Rollout

1. Start with `evaluate_only` and one reproducible `runbook` or `regression_fixture` candidate.
2. Inspect cost, timeout, environment preflight, sealed evaluator, and canary evidence.
3. Enable `auto_low_risk` only after stable evidence, with the smallest `auto_asset_kinds` allowlist.
4. Regularly review retained-asset usage, expiry, conflict, and negative transfer.
5. Keep code, Workflow, Provider-route, and tool-capability changes on proposal -> owner approval -> controlled apply.

Related paths:

- [Autoresearch](10-autoresearch-usage.en.md): verified diagnostics, repair, and Learn inputs;
- [Supervisor Inspection](12-supervisor-inspection-usage.en.md): attention and recovery candidates;
- [Metrics, Observability, and Operations](21-metrics-observability-operations.en.md): Event/Log/Metric/Delivery boundaries;
- [Context, Artifacts, and Handoff](operations/context-handoff-artifacts.en.md): required reads, lineage, and recovery handoff.
