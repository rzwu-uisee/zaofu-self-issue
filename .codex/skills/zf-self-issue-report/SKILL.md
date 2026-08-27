---
name: zf-self-issue-report
description: "Create a read-only, evidence-backed ZaoFu Self-Issue report. Use when a user enters /issue, asks ZaoFu to report its own bug, or a worker or runtime module observes a ZaoFu failure that should be organized into a provider-neutral Issue Draft without fixing code or publishing automatically."
---

# ZaoFu Self-Issue Report

Use the single Kernel-owned Self-Issue path. The reporter supplies facts; the
Kernel owns Intake, Draft, evidence refs, events, credentials, confirmations,
and provider effects.

## Report flow

1. Start `self-issue-capture`. `/issue <text>` uses the text only as the
   editable title seed. Do not create a Draft before all required Intake
   answers are submitted.
   A Kernel allowlisted strong signal may instead create a local
   `system_detected` Intake candidate. It still uses the same questions and
   cannot create a Draft or publish before user review.
2. Preserve the eight canonical Intake answers. Treat raw answers and selected
   attachments as local-only until the Kernel creates an explicit disclosure
   preview.
3. Read [evidence-policy.md](references/evidence-policy.md). If a worker found
   the failure, that worker produces bounded local sidecars and submits their
   immutable refs with its action intent. Never copy raw logs into the intent.
   Automatic worker reporting uses `worker.self_issue.detected`; every ref must
   be actor-owned, digest-bound, and carry a source event ID.
4. Respect Runtime ownership. When the project Runtime is stopped or unknown,
   stop after Kernel mechanical collection and wait. Do not start a semantic
   Agent from Web or CLI and do not label Web activity as `orchestrator`.
   Continue only after the live Runtime claims the pending assessment, or after
   the user explicitly selects a limited report.
5. Read [assessment-workspace.md](references/assessment-workspace.md). The
   existing configured `orchestrator` role assesses evidence in the supplied
   read-only committed-source workspace. It is not a separate diagnostic agent.
6. Separate observations, hypotheses, counter-evidence, and unknowns. Use
   `unknown`, `unverified`, and low confidence when evidence is incomplete.
7. Read [draft-and-provider-contract.md](references/draft-and-provider-contract.md)
   and return exactly one `self-issue-assessment.v1` object without prose.
   Publication may target locked GitLab, locked GitHub, or both, but the skill
   never chooses a destination or handles provider credentials.

When interrupted, stop promptly. Resume only from Kernel-supplied immutable
input and checkpoint refs; never treat partial model text as a completed result.
A limited report never invokes this skill for semantic assessment and must say
that assessment was not performed.

## Hard boundaries

- Do not read, request, retain, or transmit Forge tokens.
- Do not write canonical stores or `events.jsonl`; submit controlled action
  intent only.
- Do not edit source, fix code, publish, commit, merge, deploy, restart, or roll
  back.
- Do not disclose raw logs, full Trace/config/event ledgers, identity data,
  uncommitted source/patches, credentials, or unclassified fields.
- Treat failed redaction, changed refs, and unknown provenance as local-only and
  fail closed.
- Treat incident fingerprinting and external publication idempotency as
  independent state paths.
