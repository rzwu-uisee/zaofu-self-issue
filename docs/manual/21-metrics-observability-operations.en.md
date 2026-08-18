# Metrics, Observability, and Operations Guide

[中文](21-metrics-observability-operations.md) · [Operations index](operations/README.en.md)

> This guide explains how to activate, read back, diagnose, and roll back the implemented P0/P1
> observability capabilities. They are default-off or read-only. They belong to the operations
> observation plane and never take authority over Tasks, Gates, Runs, Delivery Graphs, or evidence.

## 1. Separate Five Signal Types

Do not put every diagnostic into one page or one metric family. ZaoFu separates signals by source
and authority boundary:

| Question | First place to look | Signal source | Can it change delivery truth? |
|---|---|---|---|
| Is a Task/Run complete, and why was it not accepted? | Delivery, Goal Dossier, Graph | Task, Event, Artifact, and Gate projections | No; read-only explanation |
| When did an occurrence happen, who caused it, and why? | Observability -> Events / Event Logs | append-only `events.jsonl` | No |
| Why is a process, transport, or sidecar unhealthy? | Observability -> Runtime Logs | redacted `logs/runtime.jsonl` | No |
| Is a provider route, OTLP exporter, or SSE healthy? | Observability -> Operations | provider telemetry and operations projections | No |
| What are the low-cardinality system trends and alerts? | `GET /metrics`, Operations | Prometheus-format operations metrics | No |

`zf metrics snapshot` remains ZaoFu's business/evaluation snapshot. `/metrics` is a separate,
opt-in Prometheus operations endpoint. Neither replaces the other, and a green panel never proves
delivery acceptance on its own.

[![Open the WebM recording from delivery facts to operations diagnosis](assets/observability-signal-routing.png)](assets/observability-signal-routing.webm)

The image is the first frame of the WebM recording. The recording is made by Docker Playwright
against local ZaoFu Web. It shows read-only navigation and never contains a real credential, prompt,
or raw provider transcript.

## 2. Minimum Safe Configuration

Every endpoint, header, and access token appears in `zf.yaml` as an **environment variable name
only**. Do not place URLs, Bearer tokens, JSON headers, prompts, or user content in configuration,
events, screenshots, or commits.

This is a composable minimum example. Enable only the blocks you need. Everything is off by
default except `runtime_logs`, which is enabled by default but still redacted and rotated.

```yaml
observability:
  provider_telemetry:
    mode: managed
    profile_id: zaofu-managed-v1
    endpoint_env: ZF_CLAUDE_OTLP_ENDPOINT
    enable_traces: true

  runtime_logs:
    enabled: true

  metrics:
    enabled: true
    access_token_env: ZF_METRICS_TOKEN

  otlp_exporter:
    enabled: true
    endpoint_env: ZF_OTLP_ENDPOINT
    headers_env: ZF_OTLP_HEADERS # optional environment variable containing a JSON object
    interval_seconds: 15
    request_timeout_seconds: 3
    batch_size: 64
    retry_initial_seconds: 5
    retry_max_seconds: 300
    healthy_sample_rate: 0.1

  alerts:
    enabled: true
    cooldown_seconds: 300
```

Keep startup semantics separate:

- `uv run zf start` starts the runtime watcher and the opt-in observability tick. It is the route
  that schedules the OTLP exporter and attention projection.
- `uv run zf web --host 0.0.0.0 --port 8001` reads existing state and exposes Web/API only. It
  never creates an exporter, collector, or background thread.
- Validation fails closed: if managed provider telemetry, metrics, or the OTLP exporter is enabled
  without the required `*_env` name, configuration is invalid. An environment variable name cannot
  be replaced with an endpoint value.

For Provider telemetry activation and rollback, see
[Provider Native Telemetry and OTLP](22-provider-native-telemetry.en.md).

## 3. Everyday Diagnosis

### 3.1 Delivery did not progress

1. Use Delivery/Goal Dossier first to establish the actual Task, Run, Gate, and evidence state.
2. Use Observability -> Events with Task, actor, type, status, or time-window filters to locate
   the causation chain.
3. Use Event Logs for EventLog-derived semantic audit summaries; do not confuse them with raw
   process logs.
4. Only when the symptom is transport, sidecar, Provider launch, or repeated SSE gaps, inspect
   Runtime Logs and Operations.
5. Only evidence-backed recovery, replan, or a controlled action can change execution. The
   Operations panel has no write authority.

### 3.2 The page shows `degraded` or a stream gap

`degraded` makes a projection freshness, SSE continuity, or sidecar-read gap visible. It does not
automatically mean a Task failed.

```bash
uv run zf doctor
uv run zf refs verify
uv run zf task trace TASK-ID
uv run zf metrics snapshot
```

Correlate those results with Operations `Stream gaps`, `Last failure`, and Runtime Logs
`failure_class`. Restore the actual cause first, then refresh the projection. Never delete or edit
`events.jsonl` or Task JSON merely to make a page look healthy.

[![Open the WebM recording of Event Logs and Runtime Logs triage](assets/observability-runtime-log-triage.png)](assets/observability-runtime-log-triage.webm)

The image is the first frame of the WebM recording.

### 3.3 Cost, latency, or provider failures rise

- `zf metrics snapshot`: inspect Task/Role business usage, quality, and economy first.
- `Operations`: inspect provider capability, OTLP exporter backlog, sampling/drop/redaction
  counters, SSE gaps, and attention.
- `Runtime Logs`: filter by `WARN`/`ERROR`, Provider, or Task and read redacted
  process/transport diagnostics.
- `Events`: correlate the observations with Task, Run, attempt, dispatch, or controlled-action
  causation.

Do not use Task IDs, Run IDs, session IDs, absolute paths, prompts, raw error bodies, or arbitrary
user strings as Prometheus labels. The registry accepts only low-cardinality `component`, `result`,
`provider`, `operation`, `failure_class`, `role_type`, `stage`, `action_kind`, `integration`, and
`route` labels.

## 4. Runtime Logs and Event Logs

| Panel | Content | Storage | Retention and safety boundary |
|---|---|---|---|
| Event Logs | semantic audit summaries derived from the event ledger | read projection of `events.jsonl` | does not replace source Events; never writes canonical state |
| Runtime Logs | process, transport, sidecar, and exporter diagnostics | `<state_dir>/logs/runtime.jsonl` | redacted, bounded reads, 8 MiB rotation; `.1` is the prior segment |
| Operations | capability, exporter, metric, alert, and SSE summary | rebuildable projection/sidecar | excludes endpoints, headers, prompts, transcripts, and secrets |

The Runtime Logs read route is:

```text
GET /api/projects/<project_id>/observability/runtime-logs
```

Use bounded `limit` plus `level`, `provider`, and `task_id` filters. This API and the Operations
API are read-only; `POST` should return `405`.

## 5. Prometheus `/metrics` Operations Use

The Web process exposes `/metrics` only when `observability.metrics` is enabled. It is disabled by
default; once enabled it still requires `X-ZF-Metrics-Token`, otherwise it returns `403`.

```bash
# Do not put the token in shell history, documentation, or recordings.
curl -H "X-ZF-Metrics-Token: $ZF_METRICS_TOKEN" \
  http://127.0.0.1:8001/metrics
```

This endpoint is for a trusted network or controlled collector. Its metrics are low-cardinality
operations signals for trends and alerts; they cannot carry business-object identities and no
monitoring system may write them back as ZaoFu Task/Gate/Run truth.

## 6. Verify, Read Back, and Roll Back

| Operation | Readback | Rollback |
|---|---|---|
| Enable Runtime Logs | page shows redacted/filterable rows; state contains `logs/runtime.jsonl` | set `runtime_logs.enabled: false`; retain prior rows for audit |
| Enable metrics | `/metrics` returns Prometheus text with a token; no token yields `403` | set `metrics.enabled: false` and restart Web/runtime |
| Enable Provider telemetry | Operations reports route capability, join, and reason | set mode to `off` or remove the block; start a new runtime generation |
| Enable OTLP exporter | Operations reports health, backlog, success/failure, and policy counters | set `otlp_exporter.enabled: false`; retain cursor/failure evidence and do not alter Task/Event |
| Enable alerts | Operations reports attention count; Events/Logs explain the trigger | set `alerts.enabled: false`; historical attention remains audit evidence |

Use a short canary against isolated state and a controlled collector first. Verify redaction, the
token gate, health/backoff, and unchanged Delivery before expansion. A real Provider/collector
canary cannot be replaced by unit tests or a green Web panel.

## 7. Related Guides

- [Web Dashboard use](06-web-observability-e2e.en.md)
- [Observe a delivery](operations/observe-delivery.en.md)
- [Provider Native Telemetry and OTLP](22-provider-native-telemetry.en.md)
- [Web maintenance and E2E validation](operations/web-maintainer-validation.en.md)
- [Capability coverage](reference/capability-coverage.en.md)
