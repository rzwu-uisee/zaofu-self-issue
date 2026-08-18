---
name: zf-record-browser-demo
description: "Use on demand to record a short, truthful browser workflow as deterministic frames plus an optimized GIF and provenance artifact. Reuses ZaoFu's Docker Playwright and evidence contracts; the GIF demonstrates one run but never replaces browser assertions or publishes remote assets automatically."
stages: [verify, test, demo]
tags: [browser, playwright, gif, evidence]
dependencies: [zf-browser-e2e-contract, zf-harness-evidence-collection]
auto_inject: false
load_on_demand: true
---

# ZaoFu Record Browser Demo

Record three to six meaningful states from one isolated browser run, encode
them deterministically, and bind the media to enough provenance that another
person can tell what it actually proves.

This skill adds presentation evidence. The loaded `zf-browser-e2e-contract`
still owns browser runner shape and environment classification;
`zf-harness-evidence-collection` owns auditable evidence refs. A GIF is never a
substitute for DOM/API/trace assertions or a terminal gate.

## Trigger And Boundary

Use when the operator asks for a GIF/demo, when a task explicitly requires one,
or when a browser-visible change needs compact review evidence. Do not require a
real-provider GIF for every UI change by default. A mock/fixture demo is valid
only when labelled as such and cannot prove the real-provider path.

Recording creates local frame, GIF, and provenance files. It does not commit
binary media, push an assets branch, edit a pull request, expose credentials, or
mutate canonical runtime state. Remote publication is a separate operator-
approved workflow.

## One Storyboard, One Run

1. Resolve the exact source commit and require a clean target tree for claims
   about that commit. Record `git rev-parse HEAD`, worktree path, build command,
   service URL, browser origin, viewport, provider/model mode, and whether the
   run is real, mock, or fixture-backed.
2. Start the application from that tree using the environment contract from
   `zf-browser-e2e-contract`. Use fresh runtime/browser state unless the owner
   explicitly requires an existing session.
3. Define one short storyboard with three to six semantic states such as
   initial, submitted, working, settled, and detail. Do not splice frames from
   separate server boots, provider runs, commits, origins, or browser contexts.
4. Before each capture, wait for an exact state predicate: one accessible name,
   enabled/disabled state, exact settled text, response id, URL, or stable
   `data-*` status. Fixed sleeps may pace animation but are not proof of state.
5. Name frames lexically (`00-initial.png`, `01-working.png`, ...), keep one
   viewport/crop, and exclude secrets, unrelated tabs, personal data, and
   transient notifications.
6. Keep the Playwright assertions and their trace/screenshot receipts. Capture
   the GIF frames from the same passing run when possible.

For a transient state, poll and capture in the same Playwright operation so the
state cannot settle between separate calls. For a final state, match exact text
or a stable result identity; the user's echoed prompt is not completion proof.

## Provenance Body

Write `browser-demo-provenance.v1.json` beside the GIF:

```json
{
  "schema_version": "browser-demo-provenance.v1",
  "source_commit": "<sha>",
  "worktree": "<absolute path>",
  "service_url": "<url>",
  "browser_origin": "<origin>",
  "viewport": {"width": 1440, "height": 900},
  "provider_mode": "real|mock|fixture",
  "provider_model": "<model or empty>",
  "storyboard": [{"frame": "00-initial.png", "predicate": "<exact condition>"}],
  "playwright_evidence_refs": [],
  "encoder_summary": {},
  "limitations": []
}
```

Never include token values, cookies, authorization headers, or provider request
bodies containing private data.

## Encode And Verify

The bundled script requires Python 3, `ffmpeg`, and `ffprobe`. It fails closed
when the binaries, frames, dimensions, duration, animation, or byte budget are
invalid; it never installs host dependencies.

```bash
export ZF_BROWSER_DEMO_SKILL=/absolute/path/to/skills/zf-record-browser-demo
python3 "$ZF_BROWSER_DEMO_SKILL/scripts/encode_gif.py" \
  /absolute/path/to/frames /absolute/path/to/demo.gif \
  --durations 1.5,1.5,1.5,3.5 --fps 10 --max-width 1200 --colors 128
```

If the host lacks media binaries, use an owner-approved pinned tool container
or report the blocker; do not silently install packages. Read the JSON summary,
inspect the encoded GIF (not only source frames), confirm final-state hold and
text legibility, and run `git status --short` to ensure media stayed in the
declared evidence/scratch location.

For an active task, publish complete sidecars through the existing artifact
manifest path:

```bash
uv run zf artifact manifest create \
  --task "$TASK_ID" --role "$ROLE" \
  --kind browser_demo_gif=/absolute/path/to/demo.gif \
  --kind browser_demo_provenance=/absolute/path/to/browser-demo-provenance.v1.json \
  --skill zf-record-browser-demo --emit
```

Report the absolute local path, source commit, origin, and provider mode. State
explicitly that the GIF is demonstration evidence and name the Playwright gate
that proves correctness.

How to test: exercise the encoder with fake `ffmpeg`/`ffprobe` subprocesses,
then record one isolated mock browser storyboard and verify the provenance says
`provider_mode=mock` rather than presenting it as a real-provider proof.
