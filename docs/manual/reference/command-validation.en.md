# Documentation Command Validation

[中文](command-validation.md) · [Reference index](README.en.md)

Command validation has two layers; neither substitutes for the other:

1. `scripts/manual_commands.py` scans shell fences under `docs/manual` and
   validates each `zf` command path and option against the current argparse tree;
2. `scripts/manual-command-smoke.py` executes representative cases from
   [`command-validation-matrix.yaml`](command-validation-matrix.yaml) against a
   project with terminal evidence, recording exit codes, duration, side effects,
   and before/after state guards.

`scripts/manual-docs.py check` includes the first layer and matrix-schema checks.
The second layer depends on local completed projects; ordinary CI does not guess
their paths or invoke a real provider.

## Side-Effect Classes

| Class | Allowed on a completed project | Permitted state change |
|---|---|---|
| `completed-project-readonly` | yes | none |
| `completed-project-runtime-cache-refresh` | yes | declared config-validation caches |
| `completed-project-projection-refresh` | yes | `projections/` only |
| `completed-project-projection-output` | yes | `projections/` plus an explicit output directory |
| `snapshot-*` | snapshot only | matrix-declared projection or canonical event writes |
| `live-runtime-*` / `external-*` | no | requires a Worker credential, provider, Web service, or integration |

A read-only projection has no canonical Task, Run, or Event authority; it is not
a byte-for-byte no-write promise for the state directory. Smoke separately guards
canonical files such as `events.jsonl`, Kanban, Feature, Session, and refs.

## Run Manifest

Local project paths and real IDs do not belong in the matrix. Create a temporary
manifest for one local validation:

```yaml
schema_version: zf-manual-command-smoke-run.v1
project:
  name: completed-project
  root: /absolute/project/root
  config_path: /absolute/project/root/zf.yaml
  state_dir: /absolute/project/root/.zf
  state_kind: completed-original
  terminal_evidence:
    - event_type: run.goal.completed
      task_id: GOAL-TASK-ID
      run_id: RUN-ID
context:
  task_id: TASK-ID
  run_id: RUN-ID
cases:
  - status-workers
  - task-trace
  - events-tail
  - projection-status
```

Write the receipt outside the state directory:

```bash
uv run python scripts/manual-command-smoke.py \
  --manifest /tmp/zf-manual-command-smoke.yaml \
  --receipt-dir /tmp/zf-manual-command-smoke-receipt
```

The receipt includes terminal event IDs, per-command exit code and duration,
stdout/stderr digests, changed paths, and canonical hashes. Cases that append a
canonical event must use a `/tmp` copy. A successful `artifact read` requiring an
attempt-scoped credential can only be validated inside a real Worker dispatch.
