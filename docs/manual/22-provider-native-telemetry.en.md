# Provider Native Telemetry and OTLP

[中文](22-provider-native-telemetry.md) · [Metrics, Observability, and Operations](21-metrics-observability-operations.en.md)

> Status: `partial` / opt-in. This guide describes the actual P0/P1 implementation. It does not
> promise equivalent native telemetry for every Provider or transport. Provider-native telemetry is
> runtime diagnosis only; it never becomes ZaoFu's Event, Task, Gate, Artifact, or Delivery authority.

## 1. Current Capability Matrix

| Provider / route | `managed` profile | Correlation | Current boundary |
|---|---|---|---|
| Claude Code per-turn headless / stream-json | supported when `endpoint_env` is available | W3C `TRACEPARENT` parent-child | injects managed environment; prompt, assistant, tool content, and raw API logging are disabled by default |
| Codex | not claimed as an available managed-native profile | capability/probe required | currently reports a capability reason only; do not call ZaoFu synthetic OTLP spans Codex-native spans |
| resident tmux role | no per-task context injection | `derived_only` | one pane can cross Tasks/sessions, so a specific turn's trace context cannot be injected safely |
| `host_managed` | observes host configuration only | partial / unobserved | ZaoFu does not take over the host exporter, safety switches, or actual collector health |
| `off` | disabled | none | ZaoFu remains disabled even if the host has external configuration |

This matrix is a product contract. `active` in the UI only means a managed context was established
for a current route. It does not mean an external collector received every record; collector success,
backoff, and backlog are read separately through exporter health in Operations.

[![Open the WebM recording of Provider capability, OTLP exporter, and the Delivery boundary](assets/provider-telemetry-operations.png)](assets/provider-telemetry-operations.webm)

The image is the first frame of the WebM recording.

## 2. Enable Managed Claude Telemetry

Choose a Claude Code headless or stream-json route that can receive a per-turn environment, then
provide an endpoint through an environment variable. `endpoint_env` is the variable's name, not an
endpoint value.

```yaml
observability:
  provider_telemetry:
    mode: managed
    profile_id: zaofu-managed-v1
    endpoint_env: ZF_CLAUDE_OTLP_ENDPOINT
    enable_traces: true
```

Provide the value in a controlled service-start environment, for example via a secret manager,
systemd environment, or CI secret:

```bash
export ZF_CLAUDE_OTLP_ENDPOINT='https://collector.example.invalid/v1'
uv run zf validate --cold-start
uv run zf start
```

The endpoint above uses `.invalid` and must not be copied to production. Do not place a real
endpoint or token in command history. For a Claude turn where injection is safe, the managed profile
sets `TRACEPARENT` and necessary OTLP environment while disabling high-risk prompt, assistant, tool
content, raw API, and session/account/resource attribute collection.

These cases fail closed or degrade conservatively:

- `mode: managed` with no configured or populated `endpoint_env`: the route is disabled with
  `endpoint_env_missing`; it is never half-enabled.
- a tmux route: no turn context is injected and the reason is `tmux_route_not_per_turn`.
- a Codex route: current capability is native-probe-required/unsupported; configuration must not
  claim support by force.

## 3. Read Back a Real State

1. Run an isolated headless/stream-json Claude turn, or start the configured runtime.
2. Open `Observability -> Operations` in Web.
3. Confirm Provider, Route, Effective, Join, Signals, and Reason in the `Provider Telemetry` table.
4. Inspect `OTLP Exporter` Backlog, Last success, Last failure, and policy counters separately.
5. To determine delivery impact, return to Delivery, Task trace, and Goal Dossier. Never infer a
   Gate or Task success from the telemetry panel.

Provider telemetry is stored at `<state_dir>/projections/provider_telemetry.json`. It is a bounded,
redacted capability/binding readback and excludes endpoints, headers, prompts, replies, raw provider
transcripts, and secrets.

## 4. ZaoFu Synthetic OTLP Exporter

ZaoFu can also generate redacted synthetic spans from the EventLog and export them to an OTLP
collector. This differs from Provider-native telemetry: the former represents ZaoFu event/operation
causation; the latter is the Provider capability to receive per-turn context. They can be enabled
together or separately.

```yaml
observability:
  otlp_exporter:
    enabled: true
    endpoint_env: ZF_OTLP_ENDPOINT
    headers_env: ZF_OTLP_HEADERS
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

- the exporter reads EventLog through the runtime tick, outside the event-append hot path;
- delivery-terminal/failure signals that must be retained and healthy samples follow policy;
  `healthy_sample_rate` controls only the healthy-sample portion;
- cursor, pending batch, retry/backoff, and safe status live in runtime projections;
- collector failure is fail-open: it records health/backlog/failure and never changes TaskStore,
  Gate, Judge, Provider dispatch, or Delivery Graph;
- `headers_env` must contain a JSON object from a controlled environment; its value is never echoed
  by Web/API.

`zf start` must be running before the exporter can send. `zf web` alone does not schedule it.

## 5. Prometheus, Logs, and Alert Boundaries

Optional `observability.metrics` exposes low-cardinality `/metrics`, still gated by
`X-ZF-Metrics-Token`. Runtime Logs contain redacted process/transport/sidecar diagnostics and rotate
by default. Alerts create operations attention projections only. None of them may:

- write or replace canonical current state in Task, Feature, Session, or RoleSession stores;
- alter Workflow admission, stage gates, judge verdicts, rework routing, or controlled actions;
- output prompts, replies, tool bodies, endpoints, authorization headers, raw API responses,
  absolute paths, or Task/Run/session IDs as metric labels.

For the diagnosis path, see [Metrics, Observability, and Operations](21-metrics-observability-operations.en.md).

## 6. Canary, Rollback, and Failure Handling

Use this sequence against isolated `/tmp/zf-<purpose>-<utc-timestamp>/` state:

1. run `uv run zf validate --cold-start` to validate configuration and environment-variable names;
2. start one controlled Claude headless/stream-json turn;
3. inspect Operations capability, Runtime Log redaction, and the `/metrics` token gate;
4. observe a small synthetic-span batch against a controlled collector, then simulate a connection
   failure and confirm backoff/fail-open;
5. confirm Delivery/Task/Graph did not change because of telemetry state;
6. preserve needed evidence, emit simulation completion through the normal simulation path, stop
   runtime/Web, and clean the temporary state.

Rollback never requires deletion of canonical facts:

```yaml
observability:
  provider_telemetry:
    mode: off
  otlp_exporter:
    enabled: false
  metrics:
    enabled: false
  alerts:
    enabled: false
```

Restart a new runtime generation and verify `disabled` in Operations. Retain telemetry projections,
runtime logs, exporter cursor, and failure reasons so the rollback remains explainable; do not clean
the EventLog or Delivery evidence.

## 7. Verification Scope

Current unit/integration coverage includes managed Claude environment injection, tmux non-injection,
fail-closed configuration, log redaction, low-cardinality label enforcement, `/metrics` token gating,
exporter cursor/retry/sampling/fail-open, and read-only Web API. A real collector or a particular
Provider version's native telemetry protocol still needs an independent canary in its target
environment; this guide and test names do not make a broader compatibility claim.
