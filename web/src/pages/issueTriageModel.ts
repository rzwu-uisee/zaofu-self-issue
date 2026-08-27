import type { IssueTriageSyncState } from "../api/types";

export const ISSUE_TRIAGE_POLL_INTERVAL_MS = 300_000;
export const ISSUE_TRIAGE_MIRROR_POLL_INTERVAL_MS = 10_000;

export function issueTriageNeedsRefresh(
  sync: IssueTriageSyncState | null | undefined,
  nowMs = Date.now(),
): boolean {
  if (!sync || sync.status === "never" || !sync.last_success_at) return true;
  const lastSuccess = Date.parse(sync.last_success_at);
  return !Number.isFinite(lastSuccess) || nowMs - lastSuccess >= ISSUE_TRIAGE_POLL_INTERVAL_MS;
}

export function issueTriageSourceLabel(source: string): string {
  if (source === "self_issue") return "/issue";
  if (source === "github_web") return "GitHub";
  return "Unknown";
}
