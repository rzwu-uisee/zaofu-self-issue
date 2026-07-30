---
name: zf-channel-quick-change
description: "Use for tech_leader, dev_reviewer, or qa_analyst members in a ZaoFu quick-change Channel. Produces a bounded change recommendation and verification surface without implementing or starting a workflow."
---

# Channel Quick Change

## Shared Goal

Determine whether one requested change is small, sufficiently specified, and
safe to route through a lightweight delivery workflow.

## Role Lenses

- `tech_leader`: scope, ownership, dependencies, smallest coherent change, and
  synthesis.
- `dev_reviewer`: existing code path, duplication risk, blast radius,
  maintainability, and likely changed surfaces.
- `qa_analyst`: observable acceptance criteria, negative cases, regression
  surface, and reproducible evidence.

## Method

1. Confirm the desired behavior and explicit non-goals.
2. Identify missing facts separately from owner decisions.
3. Reject "quick" classification when the change crosses multiple independent
   ownership or migration boundaries.
4. Name focused verification tiers. Add browser E2E only when the actual change
   includes a browser user path.
5. The `tech_leader` synthesizes the result using the runtime-provided
   `channel_synthesis` JSON contract.

## Boundary

- Do not edit code, task state, `zf.yaml`, or runtime truth from Channel.
- Do not claim implementation or verification is complete.
- `recommended_workflow` is a recommendation only. Task creation and Workflow
  start require their separate controlled proposal and approval.
- Apply `grill` when translating or narrowing owner intent.
