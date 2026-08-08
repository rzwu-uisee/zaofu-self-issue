# Feishu Automation and Kanban Sync

> Status: active. This feature synchronizes read-only ZaoFu projections into
> Feishu Docs and Bitable. For the primary direct-message path, see
> [Feishu AI-Native Direct Bridge](19-feishu-ai-native-direct-bridge.en.md).

## 1. Boundary

Synchronization is one-way:

- Daily Brief, Weekly Review, and Project Monitor are summarized into a light
  overview and appended to a Feishu document.
- Project Status, Action Required, Delivery Health, and Runtime Health are
  written as structured rows in an Automation Insights Bitable.
- Kanban projections create or update rows keyed by stable `Task ID`.
- Feishu documents and tables never mutate `events.jsonl`, `kanban.json`, or task state.

The Automation document is a project cover page, not a raw log. The Bitable is
the routine operational view, with recommended Overview, Highlights, Action
Required, Delivery Health, Runtime Health, and History views. Detailed traces,
events, sessions, and task drilldown remain in ZaoFu Web and CLI.

Before synchronization, inspect Daily Brief, Weekly Review, and Project Monitor
schedule, execution health, insights, outputs, and recent runs in Web
**Automations**:

![Animated Daily, Weekly, and Project Monitor reports](assets/automations-reports.webp)

## 2. Environment

Set credentials in the shell or the repository `.env`. Shell values take
precedence:

```bash
export FEISHU_APP_ID="cli_xxx"
export FEISHU_APP_SECRET="xxx"
export FEISHU_AUTOMATION_DOCUMENT_ID="docx_xxx"
export FEISHU_AUTOMATION_BITABLE_APP_TOKEN="bascn_xxx"
export FEISHU_AUTOMATION_BITABLE_TABLE_ID="tbl_xxx"
export FEISHU_BITABLE_APP_TOKEN="bascn_xxx"
export FEISHU_BITABLE_TABLE_ID="tbl_xxx"
export FEISHU_FOLDER_TOKEN="fld_xxx"
```

URLs are also accepted and parsed automatically:

```bash
export FEISHU_AUTOMATION_DOCUMENT_URL="https://example.feishu.cn/docx/docx_xxx"
export FEISHU_AUTOMATION_BITABLE_URL="https://example.feishu.cn/base/bascn_xxx?table=tbl_xxx"
export FEISHU_BITABLE_URL="https://example.feishu.cn/base/bascn_xxx?table=tbl_xxx"
```

`FEISHU_TENANT_ACCESS_TOKEN` may be supplied directly instead of exchanging the
app ID and secret.

Use `lark-cli >= 1.0.47` as the preferred Docx/Base backend:

```bash
lark-cli --version
```

`--backend lark-cli` delegates structured resource operations to the CLI, and
`--backend mock` is deterministic test mode. Legacy `--transport real` remains
an alias for `--backend lark-cli`; do not pass both options. ZaoFu maps
credentials into the child environment and fixes Feishu brand, bot identity,
and JSON output. Secrets are not placed in argv. With only an app ID and
secret, ZaoFu mints and briefly reuses a tenant token; an explicit
`FEISHU_TENANT_ACCESS_TOKEN` takes precedence. IM, streaming cards, WebSocket,
callbacks, and approvals still use `FeishuHttpTransport`. Only the former
native Docx/Base projection clients were removed.

## 3. Project Collaboration Groups and Workspace Bridge

In addition to Automation and Kanban projections, ZaoFu can manage one Feishu
collaboration group for each Project. `bot_purposes` currently supports only
`kanban_agent` (the Kanban Agent App bot) and `run_manager` (the Run Manager App
bot), so a managed group contains the Owner and those two bots. Codex, Claude
Code, and OpenClaw are internal Channel members; they are not impersonated as
Feishu users.

Configure the Project in `zf.yaml` or its merged `feishu.yaml`:

```yaml
runtime:
  feishu_inbound:
    enabled: true

integrations:
  feishu_project_group:
    enabled: true
    auto_provision: false
    name_template: "ZaoFu - {project_name}"
    owner_open_id_env: ZF_FEISHU_PROVISIONER_OWNER_OPEN_ID
    provisioner_purpose: run_manager
    bot_purposes: [kanban_agent, run_manager]
    primary_responder: kanban_agent
```

Set the Owner identity as visible to the provisioner App and configure the two
bot applications:

```bash
export ZF_FEISHU_PROVISIONER_OWNER_OPEN_ID="ou_xxx"
export FEISHU_RUNM="cli_run_manager_app"
export FEISHU_RUNM_SECRET="..."
export FEISHU_KANBAN="cli_kanban_app"
export FEISHU_KANBAN_SECRET="..."
```

`auto_provision: false` is the default. `zf init` then registers the Project and
creates a pending binding only; it does not call Feishu. Create or attach a real
group only through one of these explicit paths:

```bash
# Inspect the locally durable Project binding.
uv run zf init --workspace default
uv run zf feishu group status

# Create a collaboration group, add members, and verify them.
uv run zf feishu group provision --workspace default --confirm

# Reuse an existing group and verify/add the required members.
uv run zf feishu group attach --chat-id oc_xxx --confirm
```

With `auto_provision: true`, the same create-and-verify sequence runs during
`zf init`. This is an explicitly configured external write, not ordinary local
initialization. The resolved `chat_id` and member verification stay in a project
runtime binding, while the Workspace builds the exact `(app_id, chat_id) ->
Project` route.

| Binding state | Meaning | Operator action |
|---|---|---|
| `pending` | Desired topology exists but Feishu has not been called | Enable auto-provision and initialize again, or run `group provision --confirm` |
| `provisioning` | Group creation/attachment and membership verification are in progress | Wait for the command; do not provision concurrently |
| `active` | Owner and every configured bot were read back successfully, and the Workspace route index rebuilt successfully | Use `zf start` to maintain the inbound sidecar |
| `repair_required` | Credentials, scopes, app-scoped Owner identity, members, or route uniqueness did not verify | Read `error` in `group status`, fix it, then provision again |

Provisioning requires `im:chat:create`, `im:chat.members:read`, and
`im:chat.members:write_only` on the provisioner bot, and `lark-cli >= 1.0.64`
for member readback. The Owner `open_id` must be observable by the provisioner
App; an ID copied from another App yields `open_id cross app`. Existing groups
are never deleted merely by disabling configuration. To adopt a group, use
`group attach --chat-id ... --confirm` rather than manually writing a runtime
`chat_id`.

After activation, run `zf start`. Projects sharing a Feishu App share one
host-level WebSocket and are selected by the exact binding index. Do not run one
bridge per Project. For diagnosis, stop the managed sidecar first, then use the
single explicit route:

```bash
uv run zf feishu bridge --watch --all-workspaces --app-id "$FEISHU_APP_ID"
```

Unmentioned group messages route to `primary_responder`; an explicit mention of
the Kanban Agent or Run Manager routes to that bot. For the conversational
surface and long-output limits, see
[Feishu AI-Native Direct Bridge](19-feishu-ai-native-direct-bridge.en.md).

## 4. Initialize Targets

Create external resources explicitly rather than during scheduled sync:

```bash
uv run zf feishu init-targets --backend lark-cli --write-env
```

Important options include `--folder-token`, `--document-title`, `--base-name`,
`--table-name`, `--automation-table-name`, `--field key=name`,
`--overwrite-env`, and `--dry-run`.

Initialization creates the Automation document, Automation Insights table and
six recommended views, and a Kanban Base/Table with stable task fields,
`Board Column`, Grid, and Kanban views. `--write-env` persists created IDs to the
uncommitted `.env`.

Preview without Feishu calls:

```bash
uv run zf feishu init-targets --dry-run
```

Test `.env` writes with mock transport:

```bash
uv run zf feishu init-targets --backend mock --write-env
```

Full initialization uses `base:app:create` and
`base:table:read/create/update/delete`. Projection into an existing table uses
at least `base:field:read/create`, `base:view:read`,
`base:view:write_only`, and `base:record:read/create/update`; real
delete-and-recreate validation also needs `base:record:delete`. Scopes alone
do not grant access to a target Base/Table; the resource must also be shared
with the app.

If only `base:view:write_only` is missing, full-sync commands may use
`--no-ensure-layouts` to keep record/field/view synchronization without
changing layout. The resident projector requires its configured structure
permissions and never silently falls back or switches applications.

## 5. Dry Run

```bash
uv run zf feishu sync-automations --dry-run
uv run zf feishu sync-automation-insights-table --dry-run
uv run zf feishu sync-kanban-table --dry-run
```

Filter one automation with `--automation daily-brief`.

## 6. Real Synchronization

Append Automation reports to a document:

```bash
uv run zf feishu sync-automations \
  --backend lark-cli \
  --document-id "$FEISHU_AUTOMATION_DOCUMENT_ID"
```

`--document-url "$FEISHU_AUTOMATION_DOCUMENT_URL"` is equivalent.

Sync Automation Insights:

```bash
uv run zf feishu sync-automation-insights-table --backend lark-cli
```

If the Automation table does not exist but the Base token does, the command can
create the table and fields, write IDs back to `.env`, and then upsert summary
and insight rows by `Row Key`.

Sync Kanban:

```bash
uv run zf feishu sync-kanban-table \
  --backend lark-cli \
  --app-token "$FEISHU_BITABLE_APP_TOKEN" \
  --table-id "$FEISHU_BITABLE_TABLE_ID"
```

Or pass `--bitable-url "$FEISHU_BITABLE_URL"`.

By default, sync includes the active board and terminal tasks from the last 30
days. It ensures `Board Column`, Grid, and Kanban views and their recommended
sorting and visible fields. Gantt is not created because current start and
completion values remain text-compatible fields rather than guaranteed date
columns. Use `--active-only` to mirror only active `kanban.json`.

Use `--no-ensure-views` to write rows without creating fields or views. Use
`--no-ensure-layouts` to preserve manually customized layouts while still
ensuring missing fields and views.

If a remote task row is deleted, it is recreated by `Task ID`. If the configured
Base or table is gone, sync can recreate it and update `.env`; add
`--no-recreate-missing` to fail instead.

Override field names for an existing table:

```bash
uv run zf feishu sync-kanban-table \
  --backend lark-cli \
  --field task_id=TaskID \
  --field title=Title \
  --field status=Status \
  --field assigned_to=Owner
```

## 7. Event-Driven Kanban Projection

Enable the managed projector for low-latency `task.status_changed` projection:

```yaml
runtime:
  feishu_projection:
    enabled: true
    backend: lark-cli
    auto_create_target: false
    poll_interval_seconds: 2
    reconcile_interval_seconds: 3600
    include_archive_days: 30
    max_actions_per_tick: 20
```

Run it once, watch it directly, or force a full reconcile:

```bash
uv run zf feishu project-kanban --once --backend lark-cli
uv run zf feishu project-kanban --watch --backend lark-cli
uv run zf feishu project-kanban --once --reconcile --backend lark-cli
```

To let the projector create a Kanban Base/Table when this project has no
target, enable the capability explicitly:

```yaml
runtime:
  feishu_projection:
    enabled: true
    backend: lark-cli
    auto_create_target: true
    base_name: "ZaoFu Kanban - my-project"
    table_name: Kanban
    time_zone: Asia/Shanghai
```

This requires `FEISHU_APP_ID`, `FEISHU_APP_SECRET` (or a tenant token), and
`FEISHU_FOLDER_TOKEN`. A one-shot equivalent is:

```bash
uv run zf feishu project-kanban \
  --once \
  --create-target-if-missing \
  --folder-token "$FEISHU_FOLDER_TOKEN"
```

The bootstrap creates fields, Grid/Kanban views, and recommended layouts, then
atomically stores non-secret target IDs at
`<project.state_dir>/integrations/feishu/kanban-target.json`. This
project-scoped target takes precedence over inherited global
`FEISHU_BITABLE_*` values. Bootstrap is single-writer and resumes an incomplete
shape on the next start without creating another Base. The option defaults to
off. A remotely deleted Base/Table still fails closed; remove the scoped
target record and reinitialize deliberately rather than silently switching to
a new resource.

`zf start` and `zf stop` own the sidecar lifecycle. Its durable cursor is under
`project.state_dir/integrations/feishu/`, and its log is
`project.state_dir/logs/feishu-kanban-projector.log`. Failures retain pending
task IDs with persisted backoff and never roll back canonical TaskStore state.
Invalid cursors, truncated event logs, and periodic reconciliation force a full
sync. Only one projector may own a project at a time.

When the local ledger is missing, sync performs an exact remote lookup by
`Task ID` or `Row Key`. One match repairs the ledger, no match creates a row,
and duplicate exact matches fail closed.

## 8. Cron

```bash
uv run zf feishu cron-template --daily-time 09:00 --hourly-minute 5
```

The default template syncs Automation and Insights daily and Kanban hourly
using `--backend lark-cli`. It writes logs under `project.state_dir/logs/` and
fixes the project root and state directory explicitly, preventing cron from
accidentally using `$PWD/.zf`.
