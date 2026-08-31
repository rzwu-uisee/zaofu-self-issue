import {
  ISSUE_TRIAGE_POLL_INTERVAL_MS,
  ISSUE_TRIAGE_MIRROR_POLL_INTERVAL_MS,
  canManageIssueRun,
  issueLabelFoldCount,
  issueTriageNeedsRefresh,
  issueTriageSourceLabel,
  nextIssueLabelSelection,
} from "../src/pages/issueTriageModel.js";
import { snapshotLoadKindForPage } from "../src/app/pageLoadPolicy.js";

function assert(condition: unknown, message: string): void {
  if (!condition) throw new Error(message);
}

const base = {
  status: "fresh",
  repository: "rzwu-uisee/zaofu-self-issue",
  repository_id: "123",
  last_attempt_at: "2026-08-25T00:00:00Z",
  last_success_at: "2026-08-25T00:00:00Z",
  rate_limit_remaining: 59,
  rate_limit_reset_at: "",
  error: "",
};

assert(!issueTriageNeedsRefresh(base, Date.parse(base.last_success_at) + ISSUE_TRIAGE_POLL_INTERVAL_MS - 1), "a fresh mirror must not poll early");
assert(issueTriageNeedsRefresh(base, Date.parse(base.last_success_at) + ISSUE_TRIAGE_POLL_INTERVAL_MS), "a stale mirror must reconcile");
assert(issueTriageNeedsRefresh({ ...base, status: "never", last_success_at: "" }), "an empty mirror must reconcile on entry");
assert(issueTriageSourceLabel("self_issue") === "/issue", "Self-Issue origin must be recognizable");
assert(issueTriageSourceLabel("github_web") === "GitHub", "GitHub-created Issues must be recognizable");
assert(ISSUE_TRIAGE_MIRROR_POLL_INTERVAL_MS < ISSUE_TRIAGE_POLL_INTERVAL_MS, "Webhook mirror reads must be faster than GitHub API reconciliation");
assert(snapshotLoadKindForPage("issue-triage") === "none", "the Issue Triage page must not trigger an expensive Kanban snapshot");
assert(nextIssueLabelSelection(null, "p1")?.[0] === "p1", "clicking a label must select it as the sole filter");
assert(nextIssueLabelSelection(["p1"], "p1") === null, "clicking the active label must return to all labels");
assert(issueLabelFoldCount(["a", "b", "c"]) === 0, "three labels must remain visible");
assert(issueLabelFoldCount(["a", "b", "c", "d", "e"]) === 2, "labels after the first three must fold into a count");
assert(canManageIssueRun("triaging") && canManageIssueRun("fix_paused"), "active and paused runs must expose Manage Run");
assert(!canManageIssueRun("verified_candidate"), "completed verification must not expose Run controls");
