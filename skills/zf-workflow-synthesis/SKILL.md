---
name: zf-workflow-synthesis
description: "Provider-neutral ZaoFu method for selecting IssueFlow, PrdFlow, or RefactorFlow from a confirmed Requirement and returning a short typed FlowSpec. Use only for Workflow Synthesis before Proposal compilation; do not decompose Tasks, edit zf.yaml, approve a Proposal, or start a Run."
---

# ZaoFu Workflow Synthesis

## Objective

Turn one confirmed Requirement into a small typed recommendation that the
deterministic Workflow Proposal compiler can admit. This is Flow selection,
not Planner Task decomposition and not Orchestrator recovery.

## Inputs

Read only the supplied current request revision, immutable Requirement,
controller catalog, adapter skill plan, registered roles/skills/profiles, and
project constraints. Treat every allowlist as closed.

## Method

1. Reject or ask a blocking question when objective, required target/source,
   acceptance criteria, or a decision-critical constraint is missing.
2. Select exactly one registered family:
   - `IssueFlow` for bounded diagnosis/repair.
   - `PrdFlow` for a product behavior or feature delivery.
   - `RefactorFlow` for source-to-target parity or structural migration.
3. Produce only a short FlowSpec with purpose and sanctioned parameters:
   `lanes`, `strictness`, and `pattern_id`.
4. Request only registered roles, skills, and execution profiles.
5. State rationale, assumptions, open questions, completion profile, and risk
   hints. Do not claim approval or execution.
6. Return exactly one JSON object matching `workflow-synthesis-result.v1`;
   emit no prose or code fence.

## Hard Boundaries

- Never emit expanded ZfConfig, arbitrary handlers, custom runtime code, Task
  objects, Task Map, approval, apply, submit, push, deploy, or terminal claims.
- Never modify `zf.yaml` or canonical state.
- Never use `Workflow`/ResearchFlow/custom composition in this version.
- A blocking open question means clarification, not a best-effort Proposal.
- Provider output may vary; the compiler digest and Kernel admission are the
  deterministic boundary.

## Output Shape

```json
{
  "schema_version": "workflow-synthesis-result.v1",
  "request_id": "current request id",
  "request_revision": 1,
  "requirement_ref": "exact supplied ref",
  "requirement_digest": "exact supplied digest",
  "selected_flow_family": "IssueFlow",
  "short_flow_spec": {
    "flow_family": "IssueFlow",
    "purpose": "bounded purpose",
    "parameters": {"lanes": 1, "strictness": "standard"}
  },
  "decision_rationale": "why this family fits",
  "assumptions": [],
  "open_questions": [],
  "requested_roles": [],
  "requested_skills": [],
  "requested_profiles": ["direct-v1"],
  "completion_profile": {
    "delivery_policy": "report_only",
    "completion_threshold": "",
    "required_artifacts": []
  },
  "risk_hints": []
}
```
