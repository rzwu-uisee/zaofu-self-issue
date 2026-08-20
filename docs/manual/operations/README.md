# 运维

[English](README.en.md) · [手册首页](../00-index.md)

- [观察一次交付](observe-delivery.md): Delivery Overview、Runs Inspector、Graph、Traces、Goal Dossier 与 Inbox。
- [Metrics、Observability 与 Operations](../21-metrics-observability-operations.md): 区分交付事实、Event Logs、Runtime Logs、Provider capability、OTLP exporter 与低基数 metrics。
- [Provider Native Telemetry 与 OTLP](../22-provider-native-telemetry.md): 受管 Claude per-turn telemetry、Codex/tmux 边界、canary、回读和回退。
- [恢复长期 Run](recover-long-running-run.md): continuation、no-progress、replan 和终态收敛。
- [上下文、Artifact 与 Handoff](context-handoff-artifacts.md): required reads、lineage 和跨 Agent 接力。
- [Web 维护与 E2E 验证](web-maintainer-validation.md): launcher、Docker Playwright、scripted/real-provider tiers。
- [故障排查](../07-troubleshooting.md): 常见运行时和宿主问题。
- [Supervisor Inspection](../12-supervisor-inspection-usage.md): 观察与 attention candidate。
- [Autoresearch](../10-autoresearch-usage.md): 重复 harness 失败的深度诊断和隔离修复。
- [自我进化与能力积累](../23-self-evolution-learning.md): 基于 Run Archive 的试验、canary、可撤销资产与复用边界。

Operator 可以观察和请求受控动作，但 Web、CLI 集成和 Agent 都不能直接改写 canonical
业务状态。
