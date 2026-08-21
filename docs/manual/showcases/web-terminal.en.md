# Web Terminal: cross-client control and offline recovery

[中文](web-terminal.md) · [Showcase index](README.en.md) · [User guide](../24-web-terminal.en.md)

[![ZaoFu Web Terminal: cross-client and offline recovery](../assets/web-terminal-introduction-poster.png)](../assets/web-terminal-introduction-zh-1080p.mp4)

[Open the player](../assets/web-terminal-showcase.html) ·
[Download 1080p](../assets/web-terminal-introduction-zh-1080p.mp4) ·
[Download 4K](../assets/web-terminal-introduction-zh-4k.mp4) ·
[Chinese captions](../assets/web-terminal-introduction-zh.vtt) ·
[Chinese transcript](../assets/web-terminal-introduction-narration-zh.txt)

## What the video proves

This is not a terminal animation or fake provider. The recorder creates a
fresh mixed Project, starts a real Herdr PTY and Codex TUI, and uses three
isolated Chromium browser contexts as terminals A, B, and C:

1. A owns the unique `Control` attachment while B observes the same Session;
2. B explicitly selects `Take over control`, A loses write access, and both
   clients receive the same Codex response;
3. B submits delayed work and goes offline while server-side Herdr, the PTY,
   and Codex continue running;
4. A receives `OFFLINE_RECOVERY_OK` 9.8 seconds later;
5. after B closes, fresh context C signs in, attaches to the same Session,
   restores its full history, and continues in control;
6. the final scene shows multi-tab naming, Dock, theme synchronization, and
   transcript-backed per-tab token/cost attribution.

Codex and Claude Code in the New Session menu are derived from the initialized
Project's effective backends, not a terminal-only provider list. This run
starts only Codex. The host's Claude Code credential was not qualified, so the
video proves Claude menu derivation but not a successful Claude session.

## Exact boundary of “cross-client” and “offline recovery”

The three clients are isolated browser contexts on one recording host, not
three physical machines. They have independent cookies, sessionStorage,
WebSockets, and network state, which covers the actual client isolation
boundary used when different browsers or devices follow the same protocol.

Only the controlling browser client is taken offline. Dashboard, Herdr, the
PTY, Codex, and the host stay online. The result proves disconnect/reattach and
device handoff; it does not claim recovery from a Dashboard restart, host
failure, or provider-process crash. Only `Stop CLI` explicitly terminates the
Session.

## Auditable result

| Item | Result |
|---|---|
| Real-interaction assertions | 8 / 8 passed |
| Isolated browser clients | 3 |
| Raw assertion frames | 12, all from one real run |
| Controller offline to response | 9.784 seconds |
| Recovery target | Same `term-b40a8725ac2a4f89` Session |
| Provider | Codex CLI 0.148.0, model `gpt-5.6-sol` |
| Usage evidence | 79,991 tokens, $0.140418 partial estimate |
| Release media | 116.96 seconds; 4K/1080p; H.264 High 25fps; AAC-LC 48kHz |
| Browser validation | Both sizes reached `readyState=4`, exposed audio, advanced playback, and passed seven nonblank distinct seek probes |
| Full decode | Audio and video passed for both 4K and 1080p |

See [provenance](../assets/web-terminal-showcase-provenance.v1.json) for scene
predicates, environment versions, media hashes, cleanup, and limitations; see
[metrics](../assets/web-terminal-showcase-metrics.v1.json) for structured
results. The MP4 is a narrated six-scene composition of 12 assertion frames
from one real run, not an unedited continuous capture. It does not replace the
Playwright assertions.

To reproduce it, use `web/scripts/record-web-terminal-demo.mjs`,
`render-web-terminal-showcase.mjs`, and
`validate-web-terminal-showcase.mjs`, following the isolation, cleanup, and
evidence rules in the
[Web PTY runbook](../../runbooks/web-pty-coding-agent-terminal.md#8-分享演示资产).
