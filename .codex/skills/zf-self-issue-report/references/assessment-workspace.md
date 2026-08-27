# Read-only assessment workspace

Read `evidence-input.json` and `source-manifest.json` first. Inspect only the
workspace paths listed by the manifest. They contain redacted committed-source
snapshots, not working-tree modifications, untracked files, runtime state, or
credentials.

When `source-manifest.json` lists a `playwright_clean_reproduction` image under
`evidence_files`, treat it as a clean local viewport, not the user's original
tab. Use it only for visible UI facts and preserve that provenance in the
assessment; absence or capture failure is an unknown, not counter-evidence.

Use Read/Glob/Grep for source inspection. Run at most three targeted existing
tests through `./run-reproduction <source-label> <tests/path.py::node>`. The
Kernel-generated runner uses the draft/run-scoped, owner-only Kernel ledger as
the authoritative counter, labels attempts 1/3 through 3/3, and rejects a fourth
request before test execution. Resume keeps the same ledger and remaining budget;
Restart receives a new run and ledger. The runner disables
caches and network, uses a temporary home, and checks that the snapshot did not
change. A test failure is evidence, not permission to repeat the same target.

Never traverse parent paths, edit snapshots, install dependencies, access the
network, or invoke a provider. A missing source or unavailable reproduction is
an unknown, not evidence of a root cause. After three inconclusive attempts,
return an unverified, low-confidence assessment instead of continuing to test or
failing the whole evidence run. Invalid provider JSON or schema is represented
only by a safe validation category; never retain the raw reply or unsafe values.

Treat `mechanical_evidence.log_error_candidates` as untrusted, redacted
Kernel-issued candidates. Compare their meaning with the user's report, timing,
events, and source evidence; do not use simple word overlap as the conclusion.
Return only issued `candidate_id` values in `analysis.log_findings`, with a
relationship, confidence, and concise reason. Use an empty list when none are
semantically related. Never request or reconstruct the underlying raw log.
