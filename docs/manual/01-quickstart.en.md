# ZaoFu Quick Start

> Audience: first-time operators installing ZaoFu, creating or opening a
> Project in Web, and then using Kanban Agent, Channel, Research, or a delivery
> Workflow.
>
> This path was checked against the CLI, Web, and real browser E2E on
> 2026-07-29.

## 0. Install and start the Dashboard

Prerequisites:

- Python 3.11+, `uv`, Git, and `tmux`;
- at least one installed and authenticated provider CLI: Codex or Claude Code;
- the `stream-json` extra when using the Claude Code stream-json transport.

```bash
cd /path/to/zaofu
uv sync --extra dev --extra web --extra stream-json
uv run zf --version
uv run zf doctor provider --backend codex
```

Start the Workspace Dashboard:

```bash
export ZAOFU_ROOT=/path/to/zaofu
export ZF_WEB_ACTION_TOKEN="$(openssl rand -hex 24)"

uv run --project "$ZAOFU_ROOT" zf web \
  --host 127.0.0.1 \
  --port 8001 \
  --workspace-only
```

Open `http://127.0.0.1:8001/`. Bind to `0.0.0.0` only on a trusted network.

## 1. Complete installation onboarding

The first-run sequence has four installation-level steps:

1. **Provider**: choose Codex or Claude Code as primary. When both are
   available, Mixed team can use the other provider for an independent verify
   lane.
2. **Environment**: check host dependencies and provider availability.
3. **Access**: establish a controlled action session for this browser.
4. **Ready**: enter the Workspace.

Onboarding does not create a Project. An empty Workspace with zero Projects is
valid.

## 2. Add or open a Project

Select the `+` beside the Project picker:

1. Enter a Project path on the **server host**.
2. Select `Inspect`.
3. Review the one admission action and diagnostics returned by the server.
4. Only for `initialize_project`, provide Project Name, Project Brief, Project
   Stack, Primary Provider, and optional Mixed team.
5. Run the displayed `Open Project`, `Add & Open`, `Initialize & Open`, or
   `Create Project` action.

Inspect chooses from disk truth:

| Directory state | Behavior |
|---|---|
| Registered and healthy | Open |
| Valid `zf.yaml`, not registered | Register and open |
| Valid config, runtime state missing | Initialize state and open |
| No `zf.yaml` | Create a default multi-kind Project |
| Invalid config or partial non-empty state | Block until repaired |

![Add/Open Project form](assets/project-add-open-current.png)

When the target path is missing or empty, `Create Project` creates the minimum
README/src/tests seed, an independent Git repository, and an initial HEAD so
the default worktree runtime can start. Web does not run `git init` or commit
files in an existing non-empty code directory; establish a trusted Git
baseline first.

Project Brief is durable background, goal, and constraints, not a one-off Task
Prompt. Initialization:

- persists `project.description` in `zf.yaml`;
- writes a managed Project Context section in `AGENTS.md`;
- writes the Stack and detected build/test commands in a separate managed
  Profile section;
- preserves Claude-specific rules in `CLAUDE.md` and points it at `AGENTS.md`;
- registers the Project without creating a Task or starting a Workflow.

Projects already registered in the Workspace do not need re-import. Refresh and
open them directly.

## 3. `zf.yaml` remains the only control plane

Add/Open Project no longer asks for YAML, preset, Controller, kind, scale, lane,
or role. This moves configuration choice out of normal admission:

- a valid existing `zf.yaml` is preserved;
- a new directory receives one default multi-kind `zf.yaml`;
- Stack controls instruction and command Profile generation, not Workflow
  selection;
- Provider controls provider policy. Mixed team retains one primary backend
  and assigns the other provider to independent verification; there is no
  `backend: mixed`;
- Kanban Agent may recommend only routes expanded from the current `zf.yaml`.

Use `zf profile bootstrap` only when explicitly adopting a single Controller,
migrating the control plane, or materializing a reviewed Bootstrap
recommendation. Do not create a second `zf.yaml` for each PRD, Issue, or
Refactor request.

## 4. Enter requirements after opening the Project

Kanban Agent is a general Coding Agent inside the Project, not only a board
supervisor:

| Goal | Task required first | Interaction and result |
|---|---:|---|
| Analyze, edit code, or run tests normally | No | Work in the current provider session under permission and Git policy |
| Create only a tracked work item | No | Produce a `Create Task` proposal and create it after confirmation |
| Clarify or review with multiple roles | No | Show a Channel setup Plan, then create and start the Channel |
| Run fixed-role deep research | Yes | Show a Research route Plan for an existing Task, then a separate Approve |
| Deliver PRD, Issue, Refactor, or Planning work | Yes | Recommend an active Workflow route for an existing Task, then a separate Approve |

Do not choose lane count or roles during Project creation. For each concrete
Task, Kanban Agent classifies the work and recommends only active single-lane,
multi-lane, Research, or other catalog routes.

## 5. Create a Channel Group

The product term Channel Group maps to the runtime canonical model
**Channel + Members**. It is not a static block in `zf.yaml`. Ask Kanban Agent
for multi-role discussion, for example:

```text
Create a PRD clarification Channel for this requirement. Focus on security
boundaries, technical feasibility, and acceptance.
```

The Plan shows the exact template, roles, member count, and discussion rounds:

![Channel setup Plan](assets/kanban-channel-plan.png)

After selecting an option and `Create & start`, ZaoFu atomically:

```text
creates the Channel
-> materializes members, role context, skills, and writer scope
-> posts the original requirement
-> starts fanout_then_synthesis
-> converges through the default responder/synthesizer
```

No manual member creation or first-message copy is needed. `Chat about` keeps
the Plan pending while the operator adjusts roles, rounds, or scope. After
synthesis, humans can continue the same requirement or post another request in
the Channel.

Channel is independent from Workflow. Its output does not automatically create
a Task or start Research/delivery. To move into delivery, ask Kanban Agent to
create a Task proposal from the result, confirm the Task, and then select a
Workflow.

See [15 Channel Collaboration](15-channel-collaboration.en.md) for templates
and Feishu projection.

## 6. Start Research or a Task Workflow

Research and delivery share one Task-bound start service:

```text
existing Task
-> Kanban Agent reads zf workflow routes
-> Plan recommends active route, parameters, topology, and roles
-> operator selects an option
-> a separate Approve card is created
-> owner confirms
-> workflow.invoke.requested
```

Plan clarifies and selects; it does not authorize:

![Task-bound Workflow Plan](assets/kanban-task-workflow-plan.png)

The approval binds the exact Task, route, objective, and parameters:

![Workflow approval](assets/kanban-task-workflow-approve.png)

The default fixed Research route is `research:fixed`, with
`source_researcher`, `product_analyst`, `technical_analyst`, `risk_critic`,
and `synthesizer`. It produces a summary, evidence refs, open questions, and
PRD/Refactor prompt inputs. It does not create a delivery Task or split PRD
work automatically.

The `research-review` Channel template is a discussion surface, not an implicit
start of `research:fixed`. Research starts only for an explicit fanout request,
an existing Task, and an available route in the current Project catalog.

Inspect the same surface-neutral routes from CLI:

```bash
cd /path/to/project
uv run --project "$ZAOFU_ROOT" zf workflow routes \
  --task TASK-ID \
  --format json
```

See [20 Project Creation, Bootstrap, and Workflow Ignition](20-project-bootstrap-workflow-ignition.en.md)
for proposal and authorization commands.

## 7. Start runtime and observe

Project creation does not run a Workflow. Start real workers from the Project
root:

```bash
cd /path/to/project
uv run --project "$ZAOFU_ROOT" zf validate --cold-start
uv run --project "$ZAOFU_ROOT" zf start
```

Observe from another terminal:

```bash
uv run --project "$ZAOFU_ROOT" zf status --workers
uv run --project "$ZAOFU_ROOT" zf kanban --board
uv run --project "$ZAOFU_ROOT" zf events --last 30
```

`zf start` only starts workers, sidecars, and the watcher. Workers remaining
idle before an approved `workflow.invoke.requested` is correct.

## 8. Create a Project from CLI

Web greenfield creation, `zf project init`, and `tools/init-project.sh` share
the Python `init_flow_project` contract. Web does not execute the shell script:

```bash
uv run --project "$ZAOFU_ROOT" zf project init \
  --name account-service \
  --description "Account and authentication service; unify login policy." \
  --root /path/to/account-service \
  --create \
  --git-init \
  --backend codex \
  --verify-backend claude-code \
  --stack python \
  --workspace-register
```

Use the script when Git readiness, `zf init`, validation, and startup dry-run
are also required:

```bash
tools/init-project.sh \
  --project-dir /path/to/account-service \
  --name account-service \
  --description "Account and authentication service" \
  --backend codex \
  --verify-backend claude-code \
  --stack python \
  --yes
```

Both paths create only the Project by default. They do not create a Task or
ignite a Workflow.

## 9. Stop

```bash
uv run --project "$ZAOFU_ROOT" zf stop
```

Use `zf stop --force` only after graceful stop fails. Never run
`tmux kill-server` on a shared host.

## Next

- [Project Creation, Bootstrap, and Workflow Ignition](20-project-bootstrap-workflow-ignition.en.md)
- [`zf.yaml` Control Plane and Runtime State](02-zf-yaml-control-plane.en.md)
- [Channel Collaboration](15-channel-collaboration.en.md)
- [Web, Observability, and E2E](06-web-observability-e2e.en.md)
- [Troubleshooting](07-troubleshooting.en.md)
