# ZaoFu CLI Reference

> Scope: a task-oriented detailed CLI reference. Command existence comes from the
> [`build_parser()` generated inventory](reference/cli-command-index.en.md); this page no longer duplicates the full surface by hand.

For a short setup path, read [Quick Start](01-quickstart.en.md). For routine
operations, read [CLI Operations](03-cli-operations.en.md). For system context,
read [Architecture](architecture.en.md).

## 1. Conventions

Run commands from the repository root:

```bash
uv run zf --help
uv run zf <command> --help
```

If the editable package is installed, `zf` can be used directly. This manual
uses `uv run zf` consistently.

The CLI enforces three boundaries:

- `zf.yaml` is the only control-plane configuration.
- Runtime state comes from `project.state_dir`, which defaults to `.zf/`.
- `events.jsonl`, canonical Stores, and required artifacts own different facts under the layered-authority model.
  Use `zf` commands or controlled actions instead of editing runtime state by hand.

Common options include `--state-dir PATH` for a specific run, `--json` or
`--format json` for machine-readable output, `--dry-run` for preview, and
`--help` for the current command contract.

## 2. Shortest Working Flow

```bash
uv run zf presets
uv run zf init --preset safe-team
uv run zf validate --cold-start
uv run zf start --dry-run --no-watch
uv run zf start
```

Submit work and inspect it:

```bash
uv run zf chat "Implement a small feature with tests, review, and final gates"
uv run zf kanban --board
uv run zf events --last 20
```

Create a deterministic task:

```bash
TASK_ID="$(uv run zf kanban add "Fix login-session expiry" --id-only)"
uv run zf kanban assign "$TASK_ID" dev
uv run zf task trace "$TASK_ID"
```

Stop the harness with `uv run zf stop`.

## 3. Configuration and Preflight

| Command | Purpose |
|---|---|
| `uv run zf presets` | List built-in presets |
| `uv run zf presets show <name>` | Print one preset as YAML |
| `uv run zf init [PATH] [--create] [--preset NAME] [--state-dir PATH]` | Initialize runtime state and instruction files |
| `uv run zf init --skip-instruction-docs` | Initialize state without creating or refreshing instruction files |
| `uv run zf init --workspace-register` | Register the initialized project in the workspace |
| `uv run zf init --env-check` | Probe the environment during initialization |
| `uv run zf profile detect` | Detect the project stack |
| `uv run zf profile recommend` | Recommend a profile or preset |
| `uv run zf profile bootstrap` | Generate bootstrap recommendations |
| `uv run zf validate --path zf.yaml` | Validate configuration |
| `uv run zf validate --cold-start` | Check cold-start readiness |
| `uv run zf validate --strict-skills` | Treat skill problems as failures |
| `uv run zf validate --strict-contracts` | Validate active task contracts |
| `uv run zf validate --architecture` | Run architecture rules |
| `uv run zf validate --instructions` | Lint instruction files |
| `uv run zf doctor workdirs` | Check workdir health |
| `uv run zf doctor panes` | Check tmux pane bindings |
| `uv run zf agents` | Detect available agent CLIs |

### 3.1 Project, Request, and workflow ignition

| Command | Purpose |
|---|---|
| `uv run zf project init --name NAME --root PATH [--description TEXT] [--stack STACK]` | Create the default multi-kind Project and context without ignition |
| `uv run zf project init --kind KIND ...` | Create an explicit single-kind Controller |
| `uv run zf workflow routes --task TASK --format json` | Query active routes available to the Task |
| `uv run zf workflow start --task TASK --route ROUTE --propose` | Create a Task-bound Workflow proposal |
| `uv run zf workflow start --proposal-event-id EVENT --apply ...` | Apply the exact proposal with operator authorization |
| `uv run zf flow intake ...` | Create a requirement intake |
| `uv run zf flow classify ...` | Classify Issue/PRD/Refactor/Feature |
| `uv run zf flow clarify --confirm ...` | Clarify and confirm the requirement snapshot |
| `uv run zf flow preflight ...` | Check Request and environment readiness |
| `uv run zf flow submit --dry-run ...` | Preview ignition admission without mutation |
| `uv run zf flow submit --apply ...` | Explicitly approve and emit the workflow invoke |

Initialization, runtime startup, and ignition are separate actions. Research
and delivery Workflows require an existing Task; `--propose` does not ignite.
See
[20 Project Creation, Bootstrap, and Workflow Ignition](20-project-bootstrap-workflow-ignition.en.md)
for complete examples and option guidance.

Before starting, at least run:

```bash
uv run zf validate --cold-start
uv run zf skills doctor
uv run zf gate list
```

## 4. Runtime Lifecycle

| Command | Purpose |
|---|---|
| `uv run zf start --dry-run` | Validate and record intended startup without real workers |
| `uv run zf start` | Start workers and run the watcher in the foreground |
| `uv run zf start --no-watch` | Spawn workers and exit without a persistent watcher |
| `uv run zf status` | Show session and task status |
| `uv run zf status --workers` | Show worker and role sessions |
| `uv run zf attach [role]` | Attach to a tmux role |
| `uv run zf logs [role] --tail 100` | Read harness or role logs |
| `uv run zf restart [role]` | Restart the harness or one role |
| `uv run zf stop` | Stop gracefully |
| `uv run zf stop --force` | Force stop and clean the lock |

`--foreground` remains a deprecated no-op alias. Stream-JSON and headless
backends may have no tmux pane; use `watch`, `events`, `trace`, and Web instead.

## 5. Features and Tasks

A Feature is a high-level user goal; a Task is an executable unit. ZaoFu has
one canonical task contract.

### 5.1 Submit a Natural-Language Goal

`zf chat` emits a `user.message` intent. Its consumer is configuration-dependent: legacy safe-team may
wake a Layer 2 Agent, while Product Flow does not guarantee Task creation or Workflow ignition and should
use a real Task plus a controlled route proposal.

```bash
uv run zf chat "Add stable Web action-token configuration, docs, and tests"
```

For a long prompt:

```bash
uv run zf chat "$(cat /tmp/zf-task-prompt.md)"
uv run zf events --last 20
uv run zf kanban --board
```

Check `events` for the durable `user.message` and a legal consumer for the active profile; check the board
for any resulting state changes; and use `task trace` only after a Task exists.

For deterministic creation, use `kanban add` instead of waiting for semantic
interpretation:

```bash
TASK_ID="$(uv run zf kanban add "Support the arch channel role" --id-only)"
uv run zf kanban assign "$TASK_ID" arch
```

For a long design or PRD, generate and validate a structured spec:

```bash
uv run zf spec prompt docs/specs/arch-channel-role.md >/tmp/zf-spec-prompt.txt
uv run zf spec merge \
  docs/specs/arch-channel-role.md \
  --frontmatter /tmp/arch-channel-role.frontmatter.json \
  --output /tmp/arch-channel-role.zf-spec.md
uv run zf spec validate /tmp/arch-channel-role.zf-spec.md --strict
uv run zf spec ingest /tmp/arch-channel-role.zf-spec.md
```

Use `chat` to record generic operator intent, `kanban add` for one known task,
and `spec ingest` for a validated batch.

### 5.2 Task Commands

| Command | Purpose |
|---|---|
| `uv run zf feature add <title>` | Create a feature |
| `uv run zf feature list [--status STATUS]` | List features |
| `uv run zf feature show <feature_id>` | Show one feature |
| `uv run zf feature update <feature_id> ...` | Update a feature |
| `uv run zf kanban --board` | Show the board |
| `uv run zf kanban --watch --board` | Watch the board |
| `uv run zf kanban add <title>` | Create a task |
| `uv run zf kanban add <title> --feature F-xxx` | Create a task under a feature |
| `uv run zf kanban add <title> --blocked-by TASK-1 TASK-2` | Create dependencies |
| `uv run zf kanban assign <task_id> <role>` | Assign a task |
| `uv run zf kanban move <task_id> <status>` | Request a state transition |
| `uv run zf kanban show <task_id>` | Show task details |
| `uv run zf kanban ready` | List ready tasks |
| `uv run zf kanban open` | List nonterminal tasks |
| `uv run zf kanban pending` | List backlog tasks |
| `uv run zf kanban export --format md` | Export a board report |
| `uv run zf kanban health --format md` | Report board health |
| `uv run zf task trace <task_id>` | Show task causality |

Moving to `done` is evidence-gated. The kernel rejects completion when required
review, test, judge, or discriminator evidence is missing.

## 6. Events, Watch, and Trace

`zf emit` appends Worker/operator facts or intent to the append-only event ledger. Registered Kernel
consumers/projectors may react; this does not make every canonical Store a projection.

| Command | Purpose |
|---|---|
| `uv run zf events --last N` | Show recent events |
| `uv run zf events --type TYPE` | Filter by event type |
| `uv run zf events trace <event_id>` | Show an event causation chain |
| `uv run zf emit <type> --task <id> --actor <role>` | Append an event |
| `uv run zf emit <type> --payload '{"k":"v"}'` | Append JSON payload |
| `uv run zf emit <type> --payload-file payload.json` | Read payload from a file |
| `uv run zf watch --last N --follow` | Tail the event log |
| `uv run zf watch --role ROLE` | Filter by actor |
| `uv run zf watch --task TASK_ID` | Filter by task |
| `uv run zf watch --type TYPE` | Filter by event type |
| `uv run zf trace show <trace_id>` | Show a delivery trace |
| `uv run zf trace operation <dispatch_id>` | Show one dispatch |
| `uv run zf trace spans` | Show span projections |
| `uv run zf trace gantt --format mermaid` | Export a Gantt or DAG view |

## 7. Handoff, Memory, and Skills

| Command | Purpose |
|---|---|
| `uv run zf handoff --format md` | Generate a handoff summary |
| `uv run zf handoff --format state-packet --task TASK_ID` | Generate a task state packet |
| `uv run zf handoff --score` | Include recovery-sufficiency scoring |
| `uv run zf memory show [role]` | Read shared or role memory |
| `uv run zf memory add <role|shared> <text>` | Add memory |
| `uv run zf memory check` | Check memory staleness |
| `uv run zf skills list` | Show resolved role skills |
| `uv run zf skills doctor` | Diagnose missing or conflicting skills |
| `uv run zf update agents-md --check` | Check the managed `AGENTS.md` block |
| `uv run zf update agents-md --write` | Regenerate the managed block |

Memory and skills are worker context, not another workflow control plane.

## 8. Gates, Metrics, and Cost

| Command | Purpose |
|---|---|
| `uv run zf gate list` | List quality gates |
| `uv run zf gate run <name>` | Run one gate |
| `uv run zf gate run all` | Run every configured gate |
| `uv run zf cost [--days 7]` | Summarize cost |
| `uv run zf cost --by-instance` | Group cost by role instance |
| `uv run zf cost --by-backend` | Group cost by backend |
| `uv run zf metrics snapshot [--format json]` | Capture long-horizon metrics |
| `uv run zf metrics snapshot --diff baseline.json` | Compare with a baseline |
| `uv run zf metrics diagnose` | Diagnose metrics |
| `uv run zf metrics decision-ratio --by-reason` | Analyze decision ratios |

Queries observe cost and metrics; runtime checks enforce hard budget blocks
before dispatch.

## 9. Workdirs, Panes, Runs, and State

| Command | Purpose |
|---|---|
| `uv run zf doctor workdirs` | Check workdirs |
| `uv run zf doctor panes` | Check pane-grid bindings |
| `uv run zf panes repair` | Repair bindings from live tmux panes |
| `uv run zf workdir repair <instance>` | Repair one instance workdir |
| `uv run zf refs verify` | Verify task and candidate refs |
| `uv run zf runs list` | List run archives |
| `uv run zf runs rebuild` | Rebuild run projections |
| `uv run zf runs reconcile` | Mark stale active runs |
| `uv run zf runs for-task <task_id>` | List runs for one task |
| `uv run zf archive-run --run-id RUN --live-state-dir PATH` | Archive live state |
| `uv run zf state clean --dry-run` | Preview projection cleanup |
| `uv run zf state clean --confirm --archive` | Archive and clean projections |
| `uv run zf state reconcile --dry-run` | Check projection consistency |

Cleanup applies only to rebuildable projections, not truth files.

## 10. Web, Workspace, and Integrations

| Command | Purpose |
|---|---|
| `tools/start-webkanban.sh --host 127.0.0.1 --port 8001` | Recommended trusted-local WebKanban launcher |
| `tools/start-webkanban.sh --port 8001 --status` | Inspect tmux, port, API, and headless policy |
| `tools/start-webkanban.sh --port 8001 --stop` | Stop the WebKanban session for that port |
| `uv run zf web --host 127.0.0.1 --port 8001` | Low-level Web debugging entry point |
| `uv run zf web --workspace-only` | Low-level workspace-only debugging entry point |
| `uv run zf workspace providers openclaw list` | Show OpenClaw bindings |
| `uv run zf workspace providers openclaw set remote --base-url URL --timeout-seconds 120` | Configure a remote binding |
| `uv run zf feishu bridge --watch` | Run the direct persistent Feishu bridge |
| `uv run zf feishu handle` | Handle a Feishu event payload |
| `uv run zf feishu push --watch` | Push events directly to Feishu |
| `uv run zf feishu serve --host 0.0.0.0 --port 8000` | Run the webhook server |
| `uv run zf feishu send-test --message "hello"` | Send a test message |
| `uv run zf feishu live-smoke --to "$CHAT_ID" --purpose kanban_agent --confirm-real-api` | Verify real auth, send, list, update, and recall with the exact purpose credential; the temporary card is recalled by default |
| `uv run zf feishu init-targets --backend lark-cli --write-env` | Create Automation and Kanban targets through lark-cli |
| `uv run zf feishu sync-automations --dry-run` | Preview Automation document output |
| `uv run zf feishu sync-automation-insights-table --dry-run` | Preview the insights table |
| `uv run zf feishu sync-kanban-table --dry-run` | Preview Kanban table output |
| `uv run zf feishu project-kanban --once --backend lark-cli` | Run one event projection tick |
| `uv run zf feishu project-kanban --once --create-target-if-missing --folder-token "$FEISHU_FOLDER_TOKEN"` | Create this project's Base/Table, then run one projection tick |
| `uv run zf feishu project-kanban --watch --backend lark-cli` | Run the persistent Kanban projector |
| `uv run zf feishu cron-template` | Generate cron examples |
| `uv run zf hook-recv --event EVENT` | Receive Claude Code hook JSON from stdin |

Web writes use token-gated controlled actions. Normal local Channel and Kanban
Agent use should start through the launcher:

```bash
tools/start-webkanban.sh --host 127.0.0.1 --port 8001
```

The launcher owns the Web build, action token, Workspace/provider environment,
Codex headless sandbox policy, tmux session, and restart behavior. Direct
`zf web` inherits only the current shell and target Project `.env`; it remains
valid for dedicated state-dir debugging but does not apply trusted-local
launcher defaults. Use `--no-build` for a restart when the Web bundle is already
current.

For a stable local action token, set `ZF_WEB_ACTION_TOKEN` in the uncommitted
repository `.env`.

## 11. Specs, Backlogs, and Operator Tools

| Command | Purpose |
|---|---|
| `uv run zf spec validate <path>` | Validate structured spec Markdown |
| `uv run zf spec ingest <path>` | Generate tasks from a spec |
| `uv run zf spec prompt <path>` | Generate a worker prompt |
| `uv run zf spec merge <path> --frontmatter fm.yaml` | Merge frontmatter into a spec |
| `uv run zf backlog audit` | Audit backlog and task documents |
| `uv run zf backlog why-not-done <task_id>` | Explain why a task is not done |
| `uv run zf backlog resume-packet <task_id>` | Generate a recovery packet |
| `uv run zf backlog integration <feature_id>` | Show feature integration state |
| `uv run zf backlog workpad <task_id>` | Show a task workpad |
| `uv run zf backlog retry-metadata <task_id>` | Show retry metadata |
| `uv run zf backlog goal <feature_id>` | Show the feature goal |
| `uv run zf guard ownership --task <task_id> --role <role>` | Check write ownership |
| `uv run zf artifact manifest create ...` | Create an artifact manifest |
| `uv run zf project review-spine --dry-run` | Preview project-spine review |
| `uv run zf bug-fix-cycle --signature SIG` | Assist a ZaoFu bug-fix cycle |
| `uv run zf autopilot tick --dry-run` | Run proposal-only self-inspection |

`backlogs/` contains local candidates. Approved active and completed sprint
documents live in `tasks/`.

## 12. Self-Eval and Autoresearch

| Command | Purpose |
|---|---|
| `uv run zf self-eval validate --contract file.yaml` | Validate a self-eval contract |
| `uv run zf self-eval run --contract file.yaml` | Run self-evaluation |
| `uv run zf autoresearch run --worktree PATH --scenario S` | Run outer autoresearch |
| `uv run zf autoresearch discover-bugs` | Discover bug candidates |
| `uv run zf autoresearch triggers scan` | Scan triggers read-only |
| `uv run zf autoresearch self-repair prepare --trigger T` | Prepare self-repair |
| `uv run zf autoresearch self-repair checkpoint --task TASK --role ROLE` | Record a repair checkpoint |
| `uv run zf autoresearch self-repair validate --repair-run RUN --passed` | Record repair validation |
| `uv run zf autoresearch loop --scenarios S1.yaml --worktree PATH` | Run a multi-round scenario loop |
| `uv run zf autoresearch campaign plan --output-dir DIR` | Generate a campaign plan |

These commands may consume temporary worktrees, tmux sessions, and provider
budget. Use `/tmp/zf-<purpose>-<utc-timestamp>/` and clean temporary resources.

## 13. Current Command Inventory

The complete top-level families and recursive subcommands are generated from argparse:

- [ZaoFu CLI command inventory](reference/cli-command-index.en.md)
- Regenerate: `uv run python scripts/manual-docs.py generate`
- Check drift: `uv run python scripts/manual-docs.py check`

Do not append another hand-maintained “complete command list” here. Focused explanations may remain, but
each example must still be verified through `uv run zf <command> --help` and focused tests.

## 14. Diagnostic Order

1. Unknown syntax: `uv run zf <command> --help`.
2. Configuration: `uv run zf validate --cold-start`.
3. Unresponsive worker: `uv run zf status --workers` and `uv run zf watch --follow`.
4. Stuck task: `uv run zf task trace <task_id>` and `uv run zf backlog why-not-done <task_id>`.
5. Workdir or pane: `uv run zf doctor workdirs` and `uv run zf doctor panes`.
6. Projection mismatch: `uv run zf state reconcile --dry-run`.
7. Project missing in Web: start from its root or register it with `zf init --workspace-register`.
