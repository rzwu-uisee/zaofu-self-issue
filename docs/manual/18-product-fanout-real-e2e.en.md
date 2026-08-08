# Product Fanout and Five-Workflow Real E2E

[中文](18-product-fanout-real-e2e.md) · [Plan, Task Map, and Dispatch](13-plan-task-map-orchestrator-dispatch.en.md)

> For maintainers and QA. The runner, Research generation fence, and
> deterministic tests are implemented, while **real-Provider 5/5 acceptance is
> still pending**. Task Pipeline v4 is implementation-complete but default-off,
> with rollout still NO-GO. Mock results, an entry turn, or a UI toast are not
> real delivery evidence.

## 1. What to Validate

| Family | Main validation focus | Success terminal |
|---|---|---|
| PRD | Plan Artifact, Task Map, Impl/Verify, Candidate, Goal Closure | exact Run `run.goal.completed`, plus applicable Task `done` |
| Issue | classification, repair Tasks, regression evidence, Candidate | same |
| Refactor | source/target roots, parity, Candidate, Goal Closure | same |
| General | static-safe route, artifact delivery, closure policy | exact Run `run.goal.completed` |
| Research | immutable generation, reader aggregate, report lineage | completed aggregate + `workflow.result.available(research_report)` + Task `done` |

`workflow.invoke.requested`, `fanout.started`, Provider-turn settlement, Agent
prose, and a green UI notification are not success terminals.

## 2. Isolation and Preconditions

Each validation Project requires:

- an immutable clean ZaoFu implementation commit;
- a clean Project seed commit;
- unique `project.state_dir`, tmux session, branch prefix, and Web port;
- no historical Task, invoke, admission, dispatch, or fanout state;
- an active route for the family, bound to the expected real Provider;
- local `mcp/playwright:latest`, with browser validation through Docker host networking;
- no hot edits to ZaoFu, `zf.yaml`, routes, or the frozen Task contract during execution.

Use `/tmp/zf-<purpose>-<utc-timestamp>/`. Reserve port `8001` for the real dev
dashboard and use `8002+` for simulations.

## 3. Suite Preflight

The repository-owned runner freezes implementation, Project, effective config,
route catalog, and Provider identity before creating business work:

```bash
PYTHONPATH=src uv run python tests/e2e/five_workflow_terminal_runner.py \
  suite-preflight \
  --project-root "$PROJECT_ROOT" \
  --state-dir "$STATE_DIR" \
  --config "$PROJECT_ROOT/zf.yaml" \
  --implementation-root "$ZAOFU_ROOT" \
  --require-backend codex \
  --check-host \
  --playwright-image mcp/playwright:latest \
  --out "$REPORT_ROOT/preflight/suite-manifest.json"
```

Stop on failure. Do not bypass it by reusing state, disabling identity checks,
or editing the report.

## 4. Freeze and Start a Case

Freeze the exact case after Task creation and before Workflow approval:

```bash
PYTHONPATH=src uv run python tests/e2e/five_workflow_terminal_runner.py \
  prepare-case \
  --suite-manifest "$REPORT_ROOT/preflight/suite-manifest.json" \
  --family issue \
  --task-id "$TASK_ID" \
  --route-id "$ROUTE_ID" \
  --out "$REPORT_ROOT/case-issue/case-manifest.json"
```

Refactor also requires distinct, non-nested, non-overlapping `--source-root`
and `--target-root` after symlink resolution. After the freeze, start exactly
one Workflow through the normal Kanban Agent, Web, or `zf workflow start`
Plan/Approve path. A second `workflow.invoke.requested` fails the case.

Research binds prompt, effective config, route/template, role, Task contract,
and Run Contract into an immutable `workflow_generation`. On config drift or
restart, the stale generation is superseded/cancelled before any reader is
redispatched.

## 5. Wait for the Real Terminal

```bash
PYTHONPATH=src uv run python tests/e2e/five_workflow_terminal_runner.py \
  wait \
  --case-manifest "$REPORT_ROOT/case-issue/case-manifest.json" \
  --timeout 900 \
  --poll 1 \
  --evidence-dir "$REPORT_ROOT/case-issue/terminal"
```

`900` seconds is a ceiling, not a fixed sleep. Every poll first validates frozen
identity and then classifies the first terminal in event order. Late diagnostic
noise after success cannot reverse the result, and a stale generation's
cancellation cannot contaminate the current Run merely because Task IDs match.

The first failure, identity drift, or timeout captures Task, Run admission,
WorkflowOperation, TaskAttempt, RoleSession, related events, artifact refs, and
diagnostics. For browser evidence, pass repository-owned
`tests/e2e/scripts/capture_five_workflow_terminal.sh` argv through
`--screenshot-argv-json`. An action token may be inherited through environment
only; never place it in argv or reports.

## 6. v3/v4 and Parallel Validation

### 6.1 Task Pipeline v4 Canary

The PRD/Issue/Refactor v4 profiles are:

```text
examples/prod/controller/issue-task-pipeline-v4-canary*.yaml
examples/prod/controller/prd-task-pipeline-v4-canary*.yaml
examples/prod/controller/refactor-task-pipeline-v4-canary*.yaml
```

All declare `preferred: false` and default `ZF_TASK_PIPELINE_MODE` to `shadow`.
A blocking test must explicitly set the environment and read back resident
orchestrator, on-demand coding roles, `task_pipeline.mode=blocking`, frozen
exact Candidate, and the partial-auto-ship fence from effective config.

Fair A/B uses a preregistered manifest and a separate clean worktree/state for
every arm:

```bash
PYTHONPATH=src uv run python tests/e2e/task_pipeline_v4_canary.py \
  --repo "$ZAOFU_ROOT" \
  --registration "$REGISTRATION_JSON" \
  --output-dir "$REPORT_ROOT/task-pipeline-ab" \
  --dry-run
```

Real execution also supplies a shell-free `--command-json` argv template. The
A/B is valid only when Provider identity, budget, input, Task Map, and
conditional roles match, and v4 has no false completion or terminal residual.
Even `CANARY_EXPAND` never enables v4 by default.

### 6.2 Four-Project Parallel Kanban Suite

After serial validation, `tests/e2e/kanban_parallel_suite.py` may run General,
Issue, PRD, and Refactor concurrently. Every case needs a distinct Project
root/state dir. General uses v3; the other three use their v4 blocking profile.
The coordinator drives external drivers, terminal observers, one bounded
recovery, and cleanup only. It creates no Tasks, emits no business events, and
is not another scheduler.

```bash
PYTHONPATH=src uv run python tests/e2e/kanban_parallel_suite.py \
  --manifest "$REPORT_ROOT/parallel-suite.json" \
  --report "$REPORT_ROOT/parallel-report.json"
```

Parallel success does not cover Research and does not replace serial 5/5.

## 7. Acceptance Checklist

- suite/case identity remains stable;
- the exact Run reaches the family success terminal and applicable Task projections become `done`;
- no duplicate invoke, stale-generation dispatch, or old-Run terminal crosses generations;
- v4 Impl/Verify/Integration receipts, Candidate freeze, and global target agree;
- required artifacts, command receipts, target commit, Goal Claims, and Dossier are readable;
- Kanban, Delivery, Trace, Graph, and Loop agree with Event/Store/Artifact facts;
- no schema violation, unexplained blocker, false completion, or terminal residual exists;
- the ZaoFu implementation checkout remains the same clean commit before and after validation.

Passing deterministic runner/fence tests proves the test tool and mechanical
contracts only. Do not claim product-level acceptance before real-Provider 5/5.

## 8. Cleanup

Emit `simulation.done` before stopping each tested Project:

```bash
uv run zf emit simulation.done --payload '{"source":"five-workflow-e2e"}'
uv run zf stop
```

Stop only that case's tmux, Web, and Provider processes. Inspect the worktree:

```bash
git -C "$WORKTREE" status --short --untracked-files=all
```

Only a clean worktree may be removed normally:

```bash
git worktree remove "$WORKTREE"
git worktree prune
```

Commit, stash, or archive dirty work and inspect it before cleanup. Never use
`git worktree remove --force`, `git branch -D`, a global tmux kill, or an
unscoped directory removal.
