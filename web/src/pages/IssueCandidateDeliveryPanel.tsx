import { Check, ExternalLink, GitPullRequest, RefreshCw, ShieldCheck, X } from "lucide-react";
import { useState } from "react";
import {
  prepareIssueCandidatePublication,
  recordIssueCandidatePullRequest,
  refreshIssueCandidatePullRequest,
  reviewIssueCandidate,
} from "../api/client";
import type { IssueCandidateDeliveryProjection } from "../api/types";

function shortSha(value: string): string {
  return value ? value.slice(0, 12) : "unknown";
}

export function IssueCandidateDeliveryPanel({
  projectId,
  issueNumber,
  delivery,
  onUpdated,
}: {
  projectId: string;
  issueNumber: number;
  delivery?: IssueCandidateDeliveryProjection;
  onUpdated: (notice: string) => Promise<void>;
}) {
  const candidate = delivery?.candidate;
  const handoff = delivery?.handoff;
  const status = delivery?.status || "verified_candidate";
  const [reason, setReason] = useState("");
  const [prUrl, setPrUrl] = useState("");
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  if (!delivery?.enabled || !candidate) return null;

  const applyReview = async (verdict: "approve" | "changes_requested" | "reject") => {
    setBusy(verdict);
    setError("");
    try {
      await reviewIssueCandidate(projectId, issueNumber, {
        verdict,
        candidate_sha: candidate.candidate_head_sha,
        source_revision: candidate.source_revision,
        reason,
      });
      await onUpdated(verdict === "approve" ? "Owner review approved the exact candidate." : "Owner review receipt recorded.");
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setBusy("");
    }
  };

  const prepare = async () => {
    setBusy("prepare");
    setError("");
    try {
      await prepareIssueCandidatePublication(projectId, issueNumber, {
        candidate_sha: candidate.candidate_head_sha,
        source_revision: candidate.source_revision,
      });
      await onUpdated("Immutable local review branch prepared; push and PR creation remain manual.");
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setBusy("");
    }
  };

  const record = async () => {
    setBusy("record");
    setError("");
    try {
      await recordIssueCandidatePullRequest(projectId, issueNumber, prUrl.trim());
      setPrUrl("");
      await onUpdated("GitHub pull request identity verified and recorded.");
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setBusy("");
    }
  };

  const refresh = async () => {
    setBusy("refresh");
    setError("");
    try {
      await refreshIssueCandidatePullRequest(projectId, issueNumber);
      await onUpdated("Pull request status synchronized from GitHub.");
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setBusy("");
    }
  };

  const canReview = !["publication_prepared", "pr_open", "pr_changes_requested", "pr_approved", "pr_closed_without_merge", "merged"].includes(status);
  const prepared = Boolean(handoff?.review_branch);
  const pullRequest = handoff?.pull_request;
  return (
    <section className="issue-candidate-delivery" aria-label="Verified candidate delivery">
      <div className="issue-triage-section-heading">
        <h4><ShieldCheck aria-hidden="true" size={16} /> Verified candidate</h4>
        <span className={`issue-candidate-status ${status === "stale" ? "danger" : ""}`}>{status.replaceAll("_", " ")}</span>
      </div>
      {status === "stale" ? <div className="issue-run-permanent-warning"><strong>Candidate is stale</strong><p>The Issue or candidate changed. Rebuild and reverify before delivery.</p></div> : null}
      <dl className="issue-candidate-facts">
        <div><dt>Candidate</dt><dd><code>{shortSha(candidate.candidate_head_sha)}</code></dd></div>
        <div><dt>Base</dt><dd><code>{shortSha(candidate.candidate_base_sha)}</code></dd></div>
        <div><dt>Target</dt><dd>{delivery.configured_repository}:{delivery.configured_base_branch}</dd></div>
        <div><dt>Merge</dt><dd>Human-controlled squash</dd></div>
      </dl>
      <details><summary>Review evidence</summary>
        <div className="issue-candidate-evidence">
          <EvidenceList title="Changed paths" values={candidate.changed_paths} />
          <EvidenceList title="Passed gates" values={candidate.quality_gates_passed} />
          <EvidenceList title="Verification commands" values={candidate.verification_commands} code />
          <EvidenceList title="Unresolved risks" values={candidate.unresolved_risks} />
        </div>
      </details>
      {canReview && status !== "stale" ? (
        <div className="issue-candidate-review">
          <label htmlFor="candidate-review-reason">Owner review note</label>
          <textarea id="candidate-review-reason" placeholder="Record review reasoning or requested changes" value={reason} onChange={(event) => setReason(event.target.value)} />
          <p className="muted">Owner approval and GitHub review remain separate receipts, even when performed by the same person.</p>
          <div className="button-row">
            <button className="icon-button primary" disabled={Boolean(busy)} type="button" onClick={() => void applyReview("approve")}><Check size={15} />Approve for PR</button>
            <button className="icon-button" disabled={Boolean(busy) || !reason.trim()} type="button" onClick={() => void applyReview("changes_requested")}>Request changes</button>
            <button className="icon-button danger" disabled={Boolean(busy) || !reason.trim()} type="button" onClick={() => void applyReview("reject")}><X size={15} />Reject</button>
          </div>
        </div>
      ) : null}
      {status === "approved_for_pr" ? (
        <button className="icon-button primary" disabled={Boolean(busy)} type="button" onClick={() => void prepare()}>
          <GitPullRequest size={15} />{busy === "prepare" ? "Preparing…" : "Prepare local review branch"}
        </button>
      ) : null}
      {prepared ? (
        <div className="issue-candidate-handoff">
          <strong>Human handoff</strong>
          <p>Review branch <code>{handoff?.review_branch}</code> is pinned to the verified candidate. ZaoFu does not push, open, approve, or merge the PR.</p>
          {handoff?.human_commands?.push ? <CommandLine label="Push" value={handoff.human_commands.push} /> : null}
          {handoff?.human_commands?.create_pr ? <CommandLine label="Open PR" value={handoff.human_commands.create_pr} /> : null}
          {!pullRequest ? <div className="issue-candidate-pr-record">
            <label htmlFor="candidate-pr-url">Created GitHub PR URL</label>
            <div><input id="candidate-pr-url" placeholder={`https://github.com/${delivery.configured_repository}/pull/123`} value={prUrl} onChange={(event) => setPrUrl(event.target.value)} />
              <button className="icon-button primary" disabled={Boolean(busy) || !prUrl.trim()} type="button" onClick={() => void record()}>Record PR</button></div>
          </div> : null}
        </div>
      ) : null}
      {pullRequest ? <div className="issue-candidate-pr-status">
        <a href={pullRequest.url} target="_blank" rel="noopener noreferrer">PR #{pullRequest.number} · {pullRequest.lifecycle.replaceAll("_", " ")} <ExternalLink size={13} /></a>
        <span>GitHub review: {pullRequest.review_status} ({pullRequest.review_count})</span>
        <button className="icon-button issue-triage-tooltip" data-tooltip="Read the current PR and review state from GitHub" disabled={Boolean(busy)} type="button" onClick={() => void refresh()}>
          <RefreshCw className={busy === "refresh" ? "spinning" : ""} size={15} />{busy === "refresh" ? "Syncing…" : "Refresh PR"}
        </button>
      </div> : null}
      {status === "merged" ? <div className="issue-candidate-merged"><Check size={16} />Merged on GitHub. The source Issue was not closed automatically.</div> : null}
      {error ? <div className="issue-triage-start-error" role="alert">{error}</div> : null}
    </section>
  );
}

function EvidenceList({ title, values, code = false }: { title: string; values: string[]; code?: boolean }) {
  return <div><strong>{title}</strong>{values.length ? <ul>{values.map((value) => <li key={value}>{code ? <code>{value}</code> : value}</li>)}</ul> : <p className="muted">None reported</p>}</div>;
}

function CommandLine({ label, value }: { label: string; value: string }) {
  return <div className="issue-candidate-command"><span>{label}</span><code>{value}</code></div>;
}
