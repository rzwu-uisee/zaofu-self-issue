# Provider Native Telemetry 与 OTLP

[English](22-provider-native-telemetry.en.md) · [Metrics、Observability 与 Operations](21-metrics-observability-operations.md)

> 状态：`partial` / opt-in。本文描述当前 P0/P1 实现的实际能力，不承诺所有 Provider 或
> transport 都有相同的原生遥测能力。Provider 原生 telemetry 是运行诊断的补充，不是
> ZaoFu 的 Event、Task、Gate、Artifact 或 Delivery 权威。

## 1. 当前能力矩阵

| Provider / route | `managed` profile | 关联方式 | 当前边界 |
|---|---|---|---|
| Claude Code 的 per-turn headless / stream-json | 支持，需 `endpoint_env` 可用 | W3C `TRACEPARENT` parent-child | 注入受管环境；默认禁止 Prompt、assistant、tool content 与原始 API logging |
| Codex | 不作为受管原生 profile 宣称可用 | capability/probe required | 当前仅报告 capability 原因；不要把 ZaoFu synthetic OTLP span 误称为 Codex 原生 span |
| tmux 常驻角色 | 不注入 per-task context | `derived_only` | 一个 pane 可能跨 Task/session，不能安全把某个 turn 的 trace context 注入其中 |
| `host_managed` | 只观察宿主已配置状态 | partial / unobserved | ZaoFu 不接管宿主 exporter、安全开关或实际 collector 健康 |
| `off` | 不启用 | 无 | 即使宿主有外部配置，ZaoFu 仍保持此能力关闭 |

这张矩阵是 product contract。页面显示 `active` 只说明一个当前 route 的受管上下文已被建立，
不等于外部 collector 已成功接收每一条数据；collector 成功、backoff 和 backlog 由 Operations
中的 exporter health 另行回读。

[![点击打开 Provider capability、OTLP exporter 与 Delivery 边界的 WebM 动态演示](assets/provider-telemetry-operations.png)](assets/provider-telemetry-operations.webm)

上图是 WebM 动态演示的首帧；点击打开原始录像。

## 2. 启用 Claude 受管 telemetry

选择能够进行 per-turn 环境注入的 Claude Code headless 或 stream-json 路由，再提供 endpoint
的环境变量。`endpoint_env` 保存的是变量名，不是 endpoint 值。

```yaml
observability:
  provider_telemetry:
    mode: managed
    profile_id: zaofu-managed-v1
    endpoint_env: ZF_CLAUDE_OTLP_ENDPOINT
    enable_traces: true
```

在受控的服务启动环境中提供值，例如由 secret manager、systemd 环境或 CI secret 注入：

```bash
export ZF_CLAUDE_OTLP_ENDPOINT='https://collector.example.invalid/v1'
uv run zf validate --cold-start
uv run zf start
```

示例 endpoint 使用 `.invalid`，不能照搬到生产。不要把真实 endpoint 或 token 写进命令历史。
ZaoFu 的受管 profile 会为能够安全注入的 Claude turn 设置 `TRACEPARENT` 和必要的 OTLP 环境，
同时关闭高风险的 Prompt、assistant、tool content、raw API、session/account/resource 属性采集。

以下情况 fail closed 或保守降级：

- `mode: managed` 但 `endpoint_env` 未配置或环境变量为空：route 显示 disabled，原因是
  `endpoint_env_missing`；不会半启用。
- tmux route：不注入 turn context，原因是 `tmux_route_not_per_turn`。
- Codex route：当前显示需要原生 probe/不受支持的 capability 原因；不要用配置强行宣称支持。

## 3. 读取一次真实状态

1. 运行一个隔离的 headless/stream-json Claude turn，或启动已配置的 runtime。
2. 在 Web 进入 `Observability -> Operations`。
3. 在 `Provider Telemetry` 表中确认 Provider、Route、Effective、Join、Signals、Reason。
4. 继续查看 `OTLP Exporter` 的 `Backlog`、`Last success`、`Last failure` 和 policy counters。
5. 若要确认交付是否受影响，回到 Delivery、Task trace、Goal Dossier；不要用 telemetry 面板推断
   Gate 或 Task 成功。

Provider telemetry 侧车保存到 `<state_dir>/projections/provider_telemetry.json`。它是有界、已脱敏
的 capability/binding readback，不包含 endpoint、header、Prompt、reply、原始 provider transcript
或 secret。

## 4. ZaoFu synthetic OTLP exporter

ZaoFu 还可以从 EventLog 生成已脱敏的 synthetic spans 并导出到 OTLP collector。它与 Provider
native telemetry 不同：前者表达 ZaoFu 的事件/操作因果，后者是 Provider 能否接收 per-turn context
的 capability。两者可以并存，也可以单独启用。

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

- exporter 通过 runtime tick 从 EventLog 读取，不处于 event append 热路径；
- delivery-terminal/失败等需要保留的信号与健康样本按策略采样；`healthy_sample_rate` 只控制健康
  样本的比例；
- cursor、pending batch、retry/backoff 和安全状态保存在 runtime projection；
- collector 故障为 fail-open：记录 health/backlog/failure，不改写 TaskStore、Gate、Judge、
  Provider dispatch 或 Delivery Graph；
- `headers_env` 的值必须是由受控环境提供的 JSON object，Web/API 不回显该值。

在 Operations 回读 exporter 前，`zf start` 必须正在运行；仅运行 `zf web` 不会调度发送。

## 5. Prometheus、日志与告警边界

可选的 `observability.metrics` 暴露低基数 `/metrics`，仍要求
`X-ZF-Metrics-Token`。Runtime Logs 记录脱敏的 process/transport/sidecar 诊断，默认轮转；
Alerts 只生成 operator attention 投影。三者都不能：

- 写入或替代 Task、Feature、Session、RoleSession 的 canonical current state；
- 改变 workflow admission、stage gate、judge verdict、rework 路由或 controlled action；
- 输出 Prompt、reply、tool body、endpoint、authorization header、raw API response、绝对路径、
  Task/Run/session ID 作为 metric label。

具体诊断路径见 [Metrics、Observability 与 Operations](21-metrics-observability-operations.md)。

## 6. Canary、回退与故障处理

推荐按以下顺序在 `/tmp/zf-<purpose>-<utc-timestamp>/` 隔离 state 上验证：

1. `uv run zf validate --cold-start` 验证配置和环境变量名；
2. 启动一条可控的 Claude headless/stream-json turn；
3. 检查 Operations capability、Runtime Logs 脱敏和 `/metrics` token gate；
4. 对可控 collector 观察一小批 synthetic spans，模拟连接失败并确认 backoff/fail-open；
5. 确认 Delivery/Task/Graph 未因 telemetry 状态而变化；
6. 结束时保留必要证据，写 simulation completion，停止 runtime/Web 并清理临时 state。

回退不需要删除任何 canonical 事实：

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

使用新 runtime generation 重启后，再在 Operations 确认 disabled 状态。保留 telemetry
projection、runtime log、exporter cursor 和失败原因，以便解释为什么回退；不要清理 EventLog 或
Delivery evidence。

## 7. 验证范围

当前单元/集成验证覆盖 Claude 受管 env 注入、tmux 不注入、配置 fail-closed、日志脱敏、
低基数 label 限制、`/metrics` token gate、exporter cursor/retry/sampling/fail-open 和只读 Web API。
外部 collector 或某个 Provider 版本的真实遥测协议仍应在目标环境中做独立 canary，不应由这份
手册或测试名称作出泛化承诺。
