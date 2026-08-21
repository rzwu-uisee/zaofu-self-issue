# Web Terminal：跨终端控制与离线恢复

[English](web-terminal.en.md) · [案例索引](README.md) · [使用手册](../24-web-terminal.md)

[![ZaoFu Web Terminal：跨终端与离线恢复](../assets/web-terminal-introduction-poster.png)](../assets/web-terminal-introduction-zh-1080p.mp4)

[在线播放页](../assets/web-terminal-showcase.html) ·
[下载 1080p](../assets/web-terminal-introduction-zh-1080p.mp4) ·
[下载 4K](../assets/web-terminal-introduction-zh-4k.mp4) ·
[中文字幕](../assets/web-terminal-introduction-zh.vtt) ·
[逐字稿](../assets/web-terminal-introduction-narration-zh.txt)

## 这段视频证明什么

这不是终端 UI 动画，也不是 fake Provider。录制器在一个全新的 mixed Project 中启动真实
Herdr PTY 和 Codex TUI，再用三个隔离 Chromium browser context 表示终端 A、B、C：

1. A 持有唯一 `Control`，B 用 `Observe` 只读连接同一个 Session；
2. B 显式 `Take over control`，A 失去写权限，双方看到同一个 Codex 响应；
3. B 提交延迟任务后切为离线，浏览器连接断开，但服务端 Herdr、PTY 与 Codex 继续运行；
4. 9.8 秒后 A 收到离线期间生成的 `OFFLINE_RECOVERY_OK`；
5. 关闭 B 后，新建的 C 重新登录，连接同一个 Session，恢复完整历史并继续控制；
6. 最后展示多 Tab、重命名、Dock、主题联动，以及按 Tab 的真实 token/cost 归因。

同时，New Session 菜单中的 Codex 与 Claude Code 来自 Project 初始化后的 effective backend，
不是在 `runtime.web_terminal` 再维护一份 Provider 名单。本次只真实启动 Codex；Claude Code
登录态未完成资格，因此视频只证明菜单派生，不声明 Claude Code 真实运行通过。

## “跨终端”和“离线恢复”的准确边界

三个客户端运行在同一录制主机的三个隔离 browser context，并不是三台实体电脑。它们具有独立
cookie、sessionStorage、WebSocket 与网络状态，因此覆盖的是 Web Terminal 真正依赖的客户端
隔离边界；换成不同电脑或浏览器时使用的是相同协议路径。

离线场景只关闭控制端浏览器网络。Dashboard、Herdr、PTY、Codex 与宿主机始终在线，所以它
证明的是“客户端断线/换设备后重新 attach”，不是 Dashboard 重启、宿主机宕机或 Provider
进程崩溃后的恢复。只有 `Stop CLI` 会显式终止对应 Session。

## 可审计结果

| 项目 | 结果 |
|---|---|
| 真实交互断言 | 8 / 8 通过 |
| 独立浏览器客户端 | 3 |
| 原始断言截图 | 12 张，单次真实运行 |
| 控制端离线至响应 | 9.784 秒 |
| 恢复目标 | 同一 `term-b40a8725ac2a4f89` Session |
| Provider | Codex CLI 0.148.0，model `gpt-5.6-sol` |
| 用量证据 | 79,991 tokens，$0.140418 partial estimate |
| 成片 | 116.96 秒；4K/1080p；H.264 High 25fps；AAC-LC 48kHz |
| 浏览器播放验证 | 两个分辨率均 `readyState=4`、音轨存在、真实播放推进、7 点 seek 非空且互异 |
| 整段解码 | 4K 与 1080p 音视频均通过 |

详细的场景 predicate、环境版本、媒体哈希、安全清理和限制见
[provenance](../assets/web-terminal-showcase-provenance.v1.json)；结构化结果见
[metrics](../assets/web-terminal-showcase-metrics.v1.json)。MP4 是同一次真实运行的 12 张断言
截图构成的六镜头中文讲解成片，并非未经剪辑的连续录屏；它不替代 Playwright 断言。

## 六个镜头

| 时间 | 内容 | 关键证据 |
|---|---|---|
| 00:00–00:19 | Project-native 入口 | 任意已授权 Project；Provider 自动派生 |
| 00:19–00:39 | 真实 Herdr PTY | Codex 返回 `ZAOFU_WEB_READY` |
| 00:39–00:56 | 跨终端 Observe | A 控制，B 只读，同一个 Session |
| 00:56–01:13 | 显式 Take over | B 接管，A 降为观察，响应一致 |
| 01:13–01:34 | 客户端离线继续 | B 离线，A 收到 `OFFLINE_RECOVERY_OK` |
| 01:34–01:57 | 新客户端恢复 | C 恢复同一历史；多 Tab、主题与用量 |

需要自行复录时，使用 `web/scripts/record-web-terminal-demo.mjs`、
`render-web-terminal-showcase.mjs` 和 `validate-web-terminal-showcase.mjs`，并遵循公开
[Web Terminal 手册](../24-web-terminal.md#分享演示)中的隔离、清理与证据说明。
