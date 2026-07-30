---
name: zf-channel-research-synthesizer
description: "Use when synthesizing a ZaoFu research-review Channel. Produces an evidence-bound research synthesis and optional workflow recommendation without starting a workflow or creating a refactor task map."
---

# Channel Research Review Synthesizer

## Purpose

Converge participant findings into one research decision draft while preserving
contradictions, uncertainty, source provenance, and owner decision items.

## Synthesis Rules

1. Deduplicate equivalent findings without dropping distinct evidence.
2. Separate confirmed facts, inferences, assumptions, and unresolved questions.
3. Preserve material dissent; do not average conflicting sources into false
   consensus.
4. For every scope or recommendation narrowing, show the original intent,
   proposed interpretation, reason, and owner decision needed. Use the common
   synthesizer's atomic question and frontier protocol.
5. Recommend ResearchFlow only when the current evidence is insufficient for a
   consequential decision.

## Output

End with one `channel_synthesis` JSON object using the runtime-provided
contract. Populate:

- `title`, `decision`, and `summary`;
- `decisions`, `assumptions`, `open_questions`, and `risks`;
- `source_refs` containing the evidence actually used;
- `recommended_workflow` only as a recommendation;
- `confidence` tied to evidence quality.

Use `acceptance_criteria` only for observable evidence needed to accept the
research conclusion. Do not turn it into an implementation backlog.

## Boundary

- Do not create a Task, invoke a Workflow, or emit workflow lifecycle events.
- Do not generate `task_map.json`, `refactor-plan.md`, or implementation slices.
- Runtime owns `channel.synthesis.proposed`, artifact persistence, consensus,
  and any later controlled workflow proposal.
