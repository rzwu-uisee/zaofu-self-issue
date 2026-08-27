# Evidence and disclosure policy

Prefer immutable local refs and bounded summaries: source event IDs, Trace refs,
Git commit/branch, dirty path names, log metadata, safe error categories,
reproduction status, screenshots already captured by the authorized runtime,
and verified `path:line` locations. Mark claims as observed, reproduced,
inferred, or unknown.

The discovering worker owns semantic incident context. It may write a sanctioned
local sidecar and submit `reporter_evidence_refs`; the Kernel verifies path and
digest before promotion. For Kernel/Web/provider failures without a worker
reporter, the Kernel collects mechanical snapshots and the configured
orchestrator role collects semantic context in its isolated workspace.
If ownership is not known, the Kernel records `reporter_fallback: orchestrator`;
the Orchestrator may classify the responsibility domain but must not impersonate
a worker reporter.

Runtime liveness gates dynamic evidence. With a stopped or unknown Runtime, the
Kernel may collect only user answers and attachments, Git/version facts,
historical events/Trace/log metadata, safe configuration summaries, and
committed-source locations. It must not start Claude, Codex, live reproduction,
or dynamic Playwright. A live Runtime may add current worker context and bounded
dynamic evidence after the Kernel Orchestrator atomically claims the request.

Automatic detection is limited to an explicit Kernel strong-signal allowlist or
an actor-owned `worker.self_issue.detected` event. It creates or updates only a
local Intake candidate. Ordinary project test failures and transient recovered
errors are not automatic Self-Issues. A dismissed fingerprint is suppressed for
24 hours; active candidates are bounded. Detection never confirms disclosure,
starts OAuth, uploads an attachment, or publishes.

Keep raw logs, full Trace/config/event ledgers, environment variables, identity
data, uncommitted source, patches, internal URLs, and uncertain values local.
Never include tokens, cookies, authorization headers, private keys, passwords,
emails, personal paths, or secret-shaped strings in Draft or publication data.
Unknown provenance blocks disclosure.

Text and JSON attachments must be UTF-8 and are redacted. PNG/JPEG screenshots
are structure-validated and metadata-stripped. Videos require explicit public
disclosure confirmation because their embedded content cannot be reliably
redacted. The user separately confirms the exact attachment manifest before any
GitLab upload, then separately confirms the final Issue snapshot.

After assessment, the Kernel may derive a bounded Markdown summary from
redacted log excerpts, Web timing, failure-event identifiers, and verified code
locations. It may also copy screenshots whose trusted capture source is
Playwright, validate their digest, and strip PNG/JPEG metadata. These are only
local disclosure candidates. They must enter the same attachment-manifest
preview and one-time confirmation path as user files. Never publish a local
sidecar path; the final Issue may reference only the GitLab upload Markdown/URL.

When the reporter requests the allowlisted `kanban_board` target, the Kernel may
run one passive Playwright capture against an explicit loopback HTTP URL only
after the live Orchestrator classifies the incident as Web/UI or names a safe
Web/Kanban component. The
capture uses a clean context without user cookies, blocks non-local requests,
masks inputs/chat/identity areas, performs no click or input, and retains no
image if secret-shaped visible text is detected. Label it
`playwright_clean_reproduction`, never `user_supplied_scene`. Capture failure is
non-fatal. A successful image remains local-only until the normal attachment
manifest preview and confirmation.

Log relevance is semantic, not a Kernel keyword verdict. The Kernel scans at
most 64 MiB of eligible local logs, preserves a separately redacted 4096-byte
tail, and extracts at most 100 redacted anomaly candidates with stable IDs,
digests, relative paths, and line numbers. The existing orchestrator role may
select at most 20 candidate IDs and label each as supporting, contradicting,
contextual, or uncertain. The Kernel verifies every selected ID and digest;
unknown or modified candidates fail closed. Only medium/high-confidence
supporting, contradicting, or contextual findings enter public evidence. If no
candidate is semantically related, say so explicitly and retain only available
log-tail context. Raw logs never enter the Agent context or publication.

Use classifications `runtime`, `kernel/state`, `provider/integration`, `web/ui`,
`configuration`, `security`, `performance`, `test/regression`, or `unknown`.
Use P0 for active catastrophic/security impact, P1 for major blocking impact,
P2 for material recoverable impact, and P3 for minor impact.
