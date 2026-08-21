# Operations

[中文](README.md) · [Manual home](../00-index.en.md)

- [Observe a delivery](observe-delivery.en.md): Delivery Overview, Runs Inspector, Graph, Traces, Goal Dossier, and Inbox.
- [Metrics, Observability, and Operations](../21-metrics-observability-operations.en.md): distinguish delivery facts, Event Logs, Runtime Logs, Provider capability, OTLP exporter, and low-cardinality metrics.
- [Provider Native Telemetry and OTLP](../22-provider-native-telemetry.en.md): managed Claude per-turn telemetry, Codex/tmux boundaries, canary, readback, and rollback.
- [Recover a long-running Run](recover-long-running-run.en.md): continuation, no-progress, replan, and terminal convergence.
- [Context, Artifacts, and Handoff](context-handoff-artifacts.en.md): required reads, lineage, and cross-agent continuation.
- [Web maintenance and E2E validation](web-maintainer-validation.en.md): launcher, Docker Playwright, scripted and real-provider tiers.
- [Troubleshooting](../07-troubleshooting.en.md): common runtime and host problems.
- [Supervisor inspection](../12-supervisor-inspection-usage.en.md): observation and attention candidates.
- [Autoresearch](../10-autoresearch-usage.en.md): deep diagnosis and isolated repair for recurring harness failures.
- [Self-evolution and capability accumulation](../23-self-evolution-learning.en.md): Run-Archive-backed trials, canaries, revocable assets, and reuse boundaries.

Operators can observe and request controlled actions. Web, integrations, CLI
agents, and workers may not mutate canonical business state directly.
