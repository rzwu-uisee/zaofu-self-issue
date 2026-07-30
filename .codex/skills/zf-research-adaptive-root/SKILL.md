---
name: zf-research-adaptive-root
description: "Execute one opt-in ZaoFu adaptive Research root operation with bounded provider-native read-only subagents, evidence reconciliation, one root result, and explicit provider-operation provenance. Use only for the registered research:adaptive-pilot route."
---

# ZaoFu Adaptive Research Root

## Purpose

Run one canonical Research operation. The Provider root may delegate bounded,
independent read-only investigations, but only the root reports to ZaoFu.

This is a pilot method. Do not claim that Provider child telemetry, credential
isolation, or tree resume is mechanically proven unless the supplied runtime
evidence demonstrates it.

## Invariants

- Use at most four Provider-native `Explore` subagents and depth exactly one.
- Use zero children when the question is too small to benefit from delegation.
- Give each child a distinct question, bounded inputs, expected evidence, and
  stop condition.
- Children are read-only. They must not call `zf`, write files, mutate runtime
  state, create canonical Tasks, commit, push, deploy, or spawn more children.
- The root joins every started child before submitting. Record failed,
  cancelled, or missing children instead of silently replacing them.
- The root is the only ZaoFu protocol actor and emits exactly one completion.
- New delivery scope is a finding or proposal. It is not a runtime graph
  mutation.

## Research Method

1. Read every controlled input and record the exact source refs.
2. Split only independent evidence questions. Prefer these lenses when useful:
   source validity, product/acceptance, implementation architecture, and risk.
3. Start bounded `Explore` children in parallel. Keep source search or Web
   access in the root when the child type cannot use it safely.
4. Join all children and reconcile contradictions against primary evidence.
5. Separate confirmed facts, inference, and unknowns.
6. Produce a decision-ready result with concrete acceptance and verification
   implications. Do not return a transcript collage.

## Root Result

The single root completion must preserve all applicable fields. The Kernel
projects that result directly; do not wait for or emit a second synthesis:

```json
{
  "summary": "Decision-ready synthesis.",
  "findings": [],
  "architecture": {},
  "acceptance_matrix": [],
  "test_matrix": [],
  "task_map": [],
  "evidence_refs": [],
  "open_questions": [],
  "prd_prompt_input": "",
  "refactor_prompt_input": "",
  "provider_operation_summary": {
    "schema_version": "provider-operation-summary.v1",
    "workflow_run_id": "<from briefing>",
    "operation_id": "<stable root operation or fanout id>",
    "provider_session_id": "<current root session id>",
    "settlement": "settled",
    "child_count": 0,
    "child_status_counts": {"completed": 0},
    "active_child_count": 0,
    "peak_parallel_agents": 0,
    "usage": {"input_tokens": 0, "output_tokens": 0},
    "cost_usd": 0,
    "measurement": "provider_reported|unavailable",
    "children": []
  }
}
```

When usage or cost is unavailable, use numeric zero only with
`measurement: "unavailable"` and explain the gap. Never fabricate measured
telemetry.

Every child provenance item should include a stable child id, objective,
status, source/evidence refs, and concise result summary. Large transcripts
remain Provider/runtime sidecars.

## Completion

Use the exact completion command from the active briefing. Replace placeholders
with the complete result, emit once, then stop. Never emit the aggregate event
or any Task terminal event directly.
