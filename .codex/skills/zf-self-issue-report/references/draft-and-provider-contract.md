# Draft and provider contract

The Intake contains the fixed eight answer fields, local attachment refs,
reporter context, and verified reporter evidence refs. The promoted provider-
neutral Draft contains `incident_fingerprint`, report fields, classification,
severity, reproduction status, component, impact scope, assessment confidence,
analysis, recommended next action, evidence refs, and publication state.

Lifecycle truth is split across three independent fields. `runtime_status` is
`live`, `stopped`, or `unknown`; `evidence_status` is `pending`,
`collecting_static`, `waiting_for_runtime`, `collecting_live`, `interrupted`,
`completed`, or `failed`; `assessment_status` is `not_started`,
`waiting_for_evidence`, `waiting_for_runtime`, `running`, `completed`, `skipped`,
or `failed`. Do not collapse these into a generic `running` state.

An Intake origin is `manual` or `system_detected`. A system-detected Intake is
an awaiting-user-review candidate with a diagnostic fingerprint, bounded signal
metadata, occurrence count, and notification decision. It is not a Draft. The
Kernel updates the same candidate for a matching fingerprint within 24 hours,
applies a six-hour notification cooldown, and lets severity escalation bypass
that cooldown. User dismissal leaves a 24-hour suppression tombstone in the
event ledger.

The Kernel exclusively owns identifiers, revision checks, canonical stores,
events, sidecar validation, disclosure/redaction gates, OAuth state, Secret
Provider access, immutable publication snapshots, one-time confirmation,
idempotency, `outcome_unknown`, and Forge calls.

Publication uses one `PublicationBatch` for the user-selected `gitlab`,
`github`, or `both` mode. The batch binds the Draft revision and exact child
payload digests to one short-lived confirmation. Each provider keeps its own
provider-neutral `PublicationIntent`, stable marker, result ref, and
`outcome_unknown` recovery state. A provider result must never be inferred
from the other provider's result.

The existing orchestrator role returns exactly:

```json
{
  "schema_version": "self-issue-assessment.v1",
  "classification": "unknown",
  "severity": "P2",
  "reproduction_status": "unverified",
  "component": "unknown",
  "impact_scope": "unknown",
  "confidence": "low",
  "analysis": {
    "observations": [],
    "hypotheses": [],
    "counter_evidence": [],
    "unknowns": [],
    "code_locations": [],
    "duplicate_assessment": "",
    "log_findings": [
      {
        "candidate_id": "logc-...",
        "relation": "supports",
        "confidence": "high",
        "reason": "Semantic relationship to the user's report."
      }
    ]
  },
  "recommended_next_action": ""
}
```

The Kernel applies this result only to the matching running evidence run and
Draft revision. Interrupt writes a local checkpoint and conservatively marks
incomplete reproduction attempts as `outcome_unknown`. Resume creates a new Draft
revision while retaining the same run, evidence snapshot, and authoritative
reproduction ledger. Restart creates a new run and fresh ledger. Late or
superseded results cannot overwrite the Draft.

Only the live Runtime Orchestrator may atomically claim a pending assessment.
The claim binds a run, Draft revision, owner process, and one claim ID; repeated
event scans or Web refreshes cannot create a second assessment. Web and CLI may
request evidence collection, check liveness, or choose a limited report, but
they never execute the semantic assessment.

`analysis.log_findings` may reference only Kernel-issued redacted anomaly
candidate IDs from the immutable evidence input. The Kernel verifies the ID,
content digest, relationship, confidence, and maximum count before producing a
public log-location summary. An empty list is valid and produces an explicit
no-semantic-match statement while preserving the independent log-tail section.

Pending, collecting, and waiting-for-runtime evidence block a full publication
preview. The user may explicitly choose a limited report when Runtime is
unavailable, or continue after interruption/failure without resuming.
The Kernel must exclude partial assessment output and render an explicit
limited/interrupted/not-collected statement in the immutable publication
snapshot. A limited report sets low confidence and `assessment_status: skipped`.

Attachments use their own immutable manifest intent and confirmation. A lost
upload response locks preparation as `outcome_unknown`; only an evidenced
Controlled Action may resolve it. Final Issue publication has a distinct
snapshot, confirmation, stable marker, and recovery lock. OAuth success resumes
only the exact confirmed operation bound to its one-time state; it never grants
blanket automatic publication.

An Intake with one or more attachments requires an explicit project-visibility
acknowledgement at submission; required question validation runs first. The
Kernel may append sanitized evidence summary and trusted Playwright screenshot
candidates after a completed run, but these have no provider authority until
the immutable attachment manifest is confirmed and uploaded. Publication
Markdown contains only provider upload links, never Kernel-local evidence refs.

GitLab.com uses Authorization Code + PKCE and the `api` scope. GitHub.com uses
the public ZaoFu GitHub App Device Flow with the configured Client ID and an
`issues:write` permission snapshot; no client secret is distributed. Device
codes remain in an owner-only Kernel transaction file and tokens remain only
in Secret Provider storage. A successful callback or Device Flow poll resumes
only its exact confirmed batch.

Binary disclosure is capability-based: GitLab-only and `both` batches upload
confirmed files to GitLab; GitHub-only batches publish the exact Markdown with
an explicit binary-omission notice. GitHub Issue publication must not call an
undocumented attachment endpoint. In `both` mode GitHub receives text while
GitLab receives the confirmed binary links.
