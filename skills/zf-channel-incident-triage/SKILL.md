---
name: zf-channel-incident-triage
description: "Use for tech_leader, qa_analyst, or security_reviewer members in a ZaoFu incident-triage Channel. Produces an evidence-backed diagnosis and safe action proposal while leaving recovery execution to Run Manager and controlled actions."
---

# Channel Incident Triage

## Goal

Establish impact, timeline, current state, likely failure class, and the safest
next action without turning Channel discussion into a recovery control plane.

## Role Lenses

- `tech_leader`: scope, owner, failure class, containment, and synthesis.
- `qa_analyst`: reproduction, expected/actual behavior, regression evidence,
  and verification of recovery.
- `security_reviewer`: exposure, privilege, secret, data, and trust-boundary
  impact.

## Method

1. Separate observed facts from hypotheses.
2. Record affected task/run/channel, source event ids, evidence refs, and time
   window.
3. Classify the next action as observe, diagnose, contain, controlled resume,
   bounded repair, rollback, or owner decision.
4. For Workflow incidents, cite an existing `safe_resume_action` or diagnosis
   result when available; do not invent one.
5. The `tech_leader` returns the runtime-provided `channel_synthesis` JSON with
   impact, evidence, decision, risks, open questions, and recommended action.

## Boundary

- Do not run `zf recover --resume-pending`, restart services, apply patches, or
  emit terminal workflow events from this role.
- Run Manager decides recovery; Autoresearch diagnoses or repairs; the Kernel
  and `ControlledActionService` apply deterministic actions.
- Escalate only when authorization, irreversible risk, or unavailable external
  context prevents a safe automated action.
