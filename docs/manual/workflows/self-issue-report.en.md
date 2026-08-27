# Report a ZaoFu problem with Self-Issue

Enter `/issue` in the Kanban Agent composer, or `/issue <title>` to seed the
editable title. ZaoFu first presents an eight-step pre-Draft Intake: title, bug
description, reproduction steps, expected behavior, attachments, OS/version,
ZaoFu version, and additional context. Required answers are title, description,
reproduction, and ZaoFu version. Back/Next edits one question at a time; final
submission jumps to the first missing required answer.
When files are selected, submission also requires the attachment-visibility
checkbox. Required answers are validated first; no checkbox is required when
the attachment list is empty.

ZaoFu may also create a local `system_detected` Intake candidate for a small,
explicit allowlist of strong internal failures or an evidence-backed
`worker.self_issue.detected` event. It never creates a Draft, uploads, starts
OAuth, or publishes automatically. Web polls for such candidates while Kanban
Agent chat remains usable.

Answers persist across refresh. Cancel permanently deletes the Intake and its
local files. After submission the Kernel creates the canonical Draft, collects a
bounded mechanical snapshot, and accepts verified sidecar refs from the worker
that found the failure. Only a live Runtime Kernel Orchestrator may atomically
claim the request and ask the existing configured orchestrator role for a
read-only assessment in an isolated committed-source workspace. Web and CLI do
not start that semantic Agent. Interrupt then
offers both **Resume from checkpoint**, which keeps the same evidence snapshot
and remaining reproduction budget, and **Restart with fresh evidence**, which
creates a new run and recollects the current mechanical scene. The activity panel
updates without a page refresh. An interrupted or failed terminal run may still continue to
preview and publication. The exact Markdown states either `Not collected because
the user interrupted evidence collection.` or `No incident evidence was
collected.`; partial raw output remains local.

Use the Draft tab to edit the report and Save draft. User Intake is arranged in
the left column; reporter and orchestrator assessment fields and activity are in
the right column. Choose GitLab.com, GitHub, or both under Publish destination;
both repository targets are centrally locked. Selecting Preview displays the
exact Markdown for each selected provider. Attachments require a separate
manifest preview and confirmation when GitLab is selected. GitHub has no
supported Issue binary-upload API: GitHub-only publishes text with an explicit
omission notice, while both uploads binaries to GitLab and sends text to GitHub.
The final provider batch requires another preview and one-time confirmation.
GitLab PKCE or GitHub App Device Flow resumes only that exact confirmed batch.
Successful publication exposes one Published & View link per provider in new tabs.
While the current canonical publication state is `published`, all Draft fields
and Save draft, Safe preview, and Restart are read-only. A notice explains the
immutability, while Published & View remains available. The exact published
preview is restored from its PublicationIntent after refresh. A historical
`published_issue_ref` alone does not lock a later non-published Draft cycle.
Draft and Preview remain freely switchable after publication; Preview reuses the
actual immutable published payload and does not prepare it again.

The completed read-only collection may add a bounded redacted log/timing/event
summary and existing trusted Playwright screenshots to that same attachment
manifest. Nothing is disclosed until the user confirms the manifest. Uploaded
items use clickable GitLab URLs; access follows the target project's visibility.
The attachment manifest shows the absolute path of ZaoFu's controlled state-dir
copy and links to a Draft-and-digest-verified local preview in a new tab. Browsers
do not disclose the originally selected absolute path, and this local path never
enters the Draft or provider payload. Final Markdown uses the provider's absolute
HTTPS URL rather than a GitLab-relative `/uploads/...` link.
Empty optional user fields remain in the exact Preview as
`(User did not provide this information.)`; impact scope and assessment
confidence are included. Kernel-local sidecar paths are never published.

For an allowlisted `kanban_board` request, the Kernel may run one passive
Playwright capture against an explicit loopback HTTP URL only after the live
Orchestrator classifies a Web/UI incident or safe Web/Kanban component. It uses a clean
context without user cookies, blocks non-local requests, masks input/chat/
identity areas, performs no click or input, and discards the image when visible
text looks secret-shaped. It is labeled `playwright_clean_reproduction`, not a
capture of the user's existing tab. Failure is non-fatal; success still enters
the attachment confirmation flow.

Automatic candidates deduplicate by diagnostic fingerprint for 24 hours, use a
six-hour notification cooldown with severity-escalation bypass, and are bounded
to ten active candidates. Dismissing a candidate suppresses its fingerprint for
24 hours. Ordinary project test failures and transient recovered errors do not
auto-trigger. Configuration:

```yaml
self_issue:
  enabled: true
  automatic_detection_enabled: true
  browser_capture_enabled: true
  browser_capture_base_url: http://127.0.0.1:8002  # optional Kernel-signal origin
```

Intake is hosted in the board workspace, so Kanban Agent chat remains usable.
If runtime is stopped, Web warns that Intake/Draft persistence and committed
source inspection still work while fresh events, logs, Traces, failure
screenshots, and live reproduction evidence may be unavailable, and suggests
`cd /path_to_project && zf start`.
Runtime, evidence, and assessment are shown as separate states. **Check runtime
again** rechecks liveness. **Continue with limited report** skips semantic
assessment, fixes confidence to low, and explicitly discloses the missing live
evidence in the final body.

CLI uses the same state path:

```bash
uv run zf issue report "Short title"
uv run zf issue report "Short title" --non-interactive
uv run zf issue answer <intake_id> --answers-file answers.json
uv run zf issue preview <draft_id> --provider gitlab  # github or both
uv run zf issue confirm <batch_id> --payload-digest <digest>
uv run zf issue publish <batch_id> --confirmation-id <confirmation_id>
```

GitLab uses Authorization Code + PKCE with the broad `api` scope. GitHub uses
the public ZaoFu GitHub App Device Flow with repository Metadata read and Issues
read/write permissions; no client secret is distributed. Device transactions
are owner-only Kernel state, and both providers' tokens remain isolated in the
Secret Provider by user, workspace, provider, and authorization domain.

Public reporters cannot directly apply repository labels. The official target
repository should enable `.github/workflows/self-issue-labels.yml` and precreate
these exact labels: `runtime`, `kernel/state`, `provider/integration`, `web/ui`,
`configuration`, `security`, `performance`, `test/regression`, `unknown`, and
`p0`, `p1`, `p2`, `p3`. The workflow reads only the stable ZaoFu marker and the
allowlisted classification/severity values from a newly opened Issue.

Raw logs, traces, configuration, identities, uncommitted source, credentials,
and uncertain fields remain local. Text/JSON attachments are redacted;
PNG/JPEG metadata is stripped; videos require explicit public-disclosure
confirmation. The assessment role cannot edit code, access the network, read
Forge tokens, or publish an Issue. The Kernel-generated runner permits three
targeted tests per evidence run and rejects a fourth before execution. A
draft/run-scoped, owner-only Kernel ledger remains authoritative across Resume;
Restart creates a fresh ledger. Activity
distinguishes each numbered start and result while showing only a validated safe
target. Three inconclusive attempts, invalid JSON, or schema drift produce a
conservative `unknown` / `unverified` / `low` assessment; raw provider output and
unsafe field values are never retained.
Empty log heartbeat lines such as `[]`, `{}`, and `null` are omitted from the
public evidence Markdown.
Log evidence preserves a separately redacted final 4096-byte context and scans
bounded files for anomaly candidates outside that tail. The Kernel issues stable
candidate IDs, paths, line numbers, and digests without deciding relevance by
keyword overlap. The existing orchestrator compares candidates semantically with
the report, events, timing, and source. Only verified medium/high-confidence
candidate selections enter public evidence; otherwise the Markdown states that no
semantically related error location was identified and keeps the available tail.
