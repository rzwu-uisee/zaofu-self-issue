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

export function githubMarkdownForDisplay(content: string, projectId: string): string {
  const normalized = content.replace(/<img\b([^>]*)\/?\s*>/gi, (original, attributes: string) => {
    const source = attributes.match(/\bsrc\s*=\s*(["'])(.*?)\1/i)?.[2]
      ?.replaceAll("&amp;", "&");
    if (!source) return original;
    let url: URL;
    try {
      url = new URL(source);
    } catch {
      return original;
    }
    const allowed = (
      url.protocol === "https:"
      && (
        (url.hostname === "github.com" && url.pathname.startsWith("/user-attachments/"))
        || url.hostname === "user-images.githubusercontent.com"
        || url.hostname === "camo.githubusercontent.com"
      )
    );
    if (!allowed) return original;
    const alt = (attributes.match(/\balt\s*=\s*(["'])(.*?)\1/i)?.[2] || "GitHub attachment")
      .replace(/[\[\]]/g, "")
      .replaceAll("&quot;", "\"");
    return `![${alt}](${url.toString()})`;
  });
  return normalized.replace(/!\[([^\]]*)\]\((https:\/\/[^\s)]+)\)/g, (original, alt: string, source: string) => {
    let url: URL;
    try {
      url = new URL(source);
    } catch {
      return original;
    }
    const allowed = (
      (url.hostname === "github.com" && url.pathname.startsWith("/user-attachments/"))
      || url.hostname === "user-images.githubusercontent.com"
      || url.hostname === "camo.githubusercontent.com"
    );
    if (!allowed) return original;
    const proxy = `/api/projects/${encodeURIComponent(projectId)}/issue-triage/attachment?url=${encodeURIComponent(url.toString())}`;
    return `[![${alt}](${proxy})](${url.toString()})`;
  });
}
