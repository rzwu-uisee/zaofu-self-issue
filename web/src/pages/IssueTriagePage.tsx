import type { CSSProperties, ReactNode } from "react";
import { useCallback, useEffect, useMemo, useState } from "react";
import { ExternalLink, GitFork, RefreshCw, Search } from "lucide-react";
import {
  getIssueTriage,
  getIssueTriageDetail,
  getIssueTriageSummary,
  refreshIssueTriage,
} from "../api/client";
import type {
  IssueTriageDetail,
  IssueTriagePageData,
  IssueTriageSummary,
} from "../api/types";
import {
  ISSUE_TRIAGE_POLL_INTERVAL_MS,
  ISSUE_TRIAGE_MIRROR_POLL_INTERVAL_MS,
  issueTriageNeedsRefresh,
  issueTriageSourceLabel,
} from "./issueTriageModel";

export type TriageTab = "issues" | "runtime";

function githubLabelStyle(name: string, colors?: Record<string, string>): CSSProperties | undefined {
  const color = String(colors?.[name] || "").replace(/^#/, "");
  if (!/^[0-9a-f]{6}$/i.test(color)) return undefined;
  const red = Number.parseInt(color.slice(0, 2), 16);
  const green = Number.parseInt(color.slice(2, 4), 16);
  const blue = Number.parseInt(color.slice(4, 6), 16);
  const luminance = (0.299 * red + 0.587 * green + 0.114 * blue) / 255;
  return {
    backgroundColor: `#${color}`,
    borderColor: `#${color}`,
    color: luminance > 0.62 ? "#172033" : "#ffffff",
  };
}

export function IssueTriagePage({
  projectId,
  runtimeInterventions,
  tab,
  onTabChange,
}: {
  projectId: string;
  runtimeInterventions: ReactNode;
  tab: TriageTab;
  onTabChange: (tab: TriageTab) => void;
}) {
  return (
    <section className="issue-triage-shell">
      <div className="issue-triage-tabs" role="tablist" aria-label="Triage domain">
        <button
          className={tab === "issues" ? "active" : ""}
          role="tab"
          type="button"
          aria-selected={tab === "issues"}
          onClick={() => onTabChange("issues")}
        >
          Issues
        </button>
        <button
          className={tab === "runtime" ? "active" : ""}
          role="tab"
          type="button"
          aria-selected={tab === "runtime"}
          onClick={() => onTabChange("runtime")}
        >
          Runtime interventions
        </button>
      </div>
      {tab === "issues" ? <IssuesTab projectId={projectId} /> : runtimeInterventions}
    </section>
  );
}

function IssuesTab({ projectId }: { projectId: string }) {
  const [summary, setSummary] = useState<IssueTriageSummary | null>(null);
  const [page, setPage] = useState<IssueTriagePageData | null>(null);
  const [detail, setDetail] = useState<IssueTriageDetail | null>(null);
  const [query, setQuery] = useState("");
  const [appliedQuery, setAppliedQuery] = useState("");
  const [group, setGroup] = useState("");
  const [state, setState] = useState("");
  const [label, setLabel] = useState("");
  const [author, setAuthor] = useState("");
  const [source, setSource] = useState("");
  const [cursor, setCursor] = useState(0);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState("");

  const filters = useMemo(() => ({
    q: appliedQuery,
    group,
    state,
    label,
    author,
    source,
    cursor,
    limit: 50,
  }), [appliedQuery, author, cursor, group, label, source, state]);

  const load = useCallback(async () => {
    const [nextSummary, nextPage] = await Promise.all([
      getIssueTriageSummary(projectId),
      getIssueTriage(projectId, filters),
    ]);
    setSummary(nextSummary);
    setPage(nextPage);
    return nextSummary;
  }, [filters, projectId]);

  const refresh = useCallback(async (showSpinner = true) => {
    if (showSpinner) setRefreshing(true);
    setError("");
    try {
      await refreshIssueTriage(projectId);
      await load();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
      await load().catch(() => undefined);
    } finally {
      if (showSpinner) setRefreshing(false);
    }
  }, [load, projectId]);

  useEffect(() => {
    let active = true;
    setLoading(true);
    setError("");
    void load()
      .then((nextSummary) => {
        if (active && issueTriageNeedsRefresh(nextSummary.sync)) void refresh(false);
      })
      .catch((cause) => {
        if (active) setError(cause instanceof Error ? cause.message : String(cause));
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => { active = false; };
  }, [load, refresh]);

  useEffect(() => {
    const timer = window.setInterval(() => {
      if (document.visibilityState === "visible") void load().catch(() => undefined);
    }, ISSUE_TRIAGE_MIRROR_POLL_INTERVAL_MS);
    return () => window.clearInterval(timer);
  }, [load]);

  useEffect(() => {
    const timer = window.setInterval(() => {
      if (document.visibilityState === "visible") void refresh(false);
    }, ISSUE_TRIAGE_POLL_INTERVAL_MS);
    return () => window.clearInterval(timer);
  }, [refresh]);

  const openDetail = async (issueNumber: number) => {
    setError("");
    try {
      setDetail(await getIssueTriageDetail(projectId, issueNumber));
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    }
  };

  const resetCursor = (setter: (value: string) => void, value: string) => {
    setter(value);
    setCursor(0);
    setDetail(null);
  };

  return (
    <section className="issue-triage-page">
      <header className="issue-triage-header">
        <div>
          <p className="eyebrow">GITHUB ISSUE MIRROR</p>
          <h2>ZaoFu Issue Triage</h2>
          <p className="muted">Read-only in ZaoFu. Edit labels, state, assignees, or content on GitHub.</p>
        </div>
        <div className="button-row">
          {summary ? (
            <a className="icon-button" href={summary.repository_url} target="_blank" rel="noopener noreferrer">
              <GitFork aria-hidden="true" size={15} /> Repository
            </a>
          ) : null}
          {summary ? (
            <a className="icon-button" href={summary.new_issue_url} target="_blank" rel="noopener noreferrer">
              New issue <ExternalLink aria-hidden="true" size={14} />
            </a>
          ) : null}
          <button className="icon-button" disabled={refreshing} type="button" onClick={() => void refresh()}>
            <RefreshCw aria-hidden="true" className={refreshing ? "spinning" : ""} size={15} />
            {refreshing ? "Syncing…" : "Refresh"}
          </button>
        </div>
      </header>

      <SyncBanner summary={summary} error={error} />

      <div className="issue-triage-metrics">
        <Metric label="Untriaged" value={summary?.groups.untriaged ?? 0} active={group === "untriaged"} onClick={() => resetCursor(setGroup, group === "untriaged" ? "" : "untriaged")} />
        <Metric label="Triaged" value={summary?.groups.triaged ?? 0} active={group === "triaged"} onClick={() => resetCursor(setGroup, group === "triaged" ? "" : "triaged")} />
        <Metric label="Closed" value={summary?.groups.closed ?? 0} active={group === "closed"} onClick={() => resetCursor(setGroup, group === "closed" ? "" : "closed")} />
        <Metric label="All issues" value={summary?.total ?? 0} active={!group} onClick={() => resetCursor(setGroup, "")} />
      </div>

      <div className="issue-triage-filters">
        <form onSubmit={(event) => { event.preventDefault(); setCursor(0); setAppliedQuery(query.trim()); }}>
          <Search aria-hidden="true" size={15} />
          <input aria-label="Search issues" placeholder="Search title, number, author, or label" value={query} onChange={(event) => setQuery(event.target.value)} />
        </form>
        <select aria-label="GitHub state" value={state} onChange={(event) => resetCursor(setState, event.target.value)}>
          <option value="">All states</option><option value="open">Open</option><option value="closed">Closed</option>
        </select>
        <select aria-label="Label" value={label} onChange={(event) => resetCursor(setLabel, event.target.value)}>
          <option value="">All labels</option>
          {Object.keys(summary?.labels ?? {}).map((item) => <option key={item} value={item}>{item}</option>)}
        </select>
        <select aria-label="Contributor" value={author} onChange={(event) => resetCursor(setAuthor, event.target.value)}>
          <option value="">All contributors</option>
          {Object.keys(summary?.authors ?? {}).map((item) => <option key={item} value={item}>{item}</option>)}
        </select>
        <select aria-label="Source" value={source} onChange={(event) => resetCursor(setSource, event.target.value)}>
          <option value="">All sources</option><option value="self_issue">/issue</option><option value="github_web">GitHub</option>
        </select>
      </div>

      <div className={`issue-triage-content ${detail ? "with-detail" : ""}`}>
        <div className="issue-triage-list" aria-busy={loading}>
          {loading ? <p className="empty-text">Loading Issue mirror…</p> : null}
          {!loading && !page?.items.length ? <p className="empty-text">No matching GitHub Issues.</p> : null}
          {page?.items.map((item) => (
            <button className={`issue-triage-row ${detail?.issue.number === item.number ? "selected" : ""}`} key={item.issue_key} type="button" onClick={() => void openDetail(item.number)}>
              <span className={`issue-state-dot ${item.github_state}`} />
              <span className="issue-triage-row-main">
                <strong>{item.title}</strong>
                <span className="muted">#{item.number} · {item.author_login} · {issueTriageSourceLabel(item.source)} · updated {new Date(item.updated_at).toLocaleString()}</span>
                <span className="issue-label-row">{item.labels.map((itemLabel) => <span className="badge issue-label-badge" key={itemLabel} style={githubLabelStyle(itemLabel, item.label_colors)}>{itemLabel}</span>)}</span>
              </span>
              <span className="issue-group-badge">{item.derived_group}</span>
            </button>
          ))}
          <div className="issue-triage-pagination">
            <button className="icon-button" disabled={!page || page.cursor === 0} type="button" onClick={() => { setCursor(Math.max(0, cursor - 50)); setDetail(null); }}>Previous</button>
            <span className="muted">{page ? `${page.cursor + (page.items.length ? 1 : 0)}–${page.cursor + page.items.length} of ${page.total}` : "0 issues"}</span>
            <button className="icon-button" disabled={page?.next_cursor == null} type="button" onClick={() => { setCursor(page?.next_cursor ?? cursor); setDetail(null); }}>Next</button>
          </div>
        </div>
        {detail ? <IssueDetail detail={detail} onClose={() => setDetail(null)} /> : null}
      </div>
    </section>
  );
}

function SyncBanner({ summary, error }: { summary: IssueTriageSummary | null; error: string }) {
  const sync = summary?.sync;
  const stale = Boolean(error || sync?.status === "failed" || sync?.status === "rate_limited");
  const status = !sync || sync.status === "never"
    ? "GitHub mirror has not synchronized yet."
    : stale
      ? "Showing the last successful local mirror."
      : sync.status === "syncing"
        ? "Synchronizing with GitHub…"
        : "GitHub mirror is current.";
  return (
    <div className={`issue-triage-sync ${stale ? "stale" : ""}`}>
      <span>{status}</span>
      <span className="muted">
        {error || sync?.error || (sync?.last_success_at ? `Last synced ${new Date(sync.last_success_at).toLocaleString()}` : "Not synchronized yet")}
      </span>
    </div>
  );
}

function Metric({ label, value, active, onClick }: { label: string; value: number; active: boolean; onClick: () => void }) {
  return <button className={`issue-triage-metric ${active ? "active" : ""}`} type="button" onClick={onClick}><strong>{value}</strong><span>{label}</span></button>;
}

function IssueDetail({ detail, onClose }: { detail: IssueTriageDetail; onClose: () => void }) {
  const { issue } = detail;
  return (
    <aside className="issue-triage-detail">
      <header>
        <div><span className="muted">GitHub Issue #{issue.number}</span><h3>{issue.title}</h3></div>
        <button className="icon-button" type="button" onClick={onClose}>Close</button>
      </header>
      <div className="button-row">
        <a className="icon-button primary" href={issue.html_url} target="_blank" rel="noopener noreferrer">Open on GitHub <ExternalLink aria-hidden="true" size={14} /></a>
        <a className="icon-button" href={issue.html_url} target="_blank" rel="noopener noreferrer">Edit on GitHub</a>
      </div>
      <dl className="issue-triage-meta">
        <div><dt>State</dt><dd>{issue.github_state}</dd></div>
        <div><dt>Group</dt><dd>{issue.derived_group}</dd></div>
        <div><dt>Reporter</dt><dd>{issue.author_login}</dd></div>
        <div><dt>Assignees</dt><dd>{issue.assignees.join(", ") || "None"}</dd></div>
        <div><dt>Milestone</dt><dd>{issue.milestone || "None"}</dd></div>
        <div><dt>Comments</dt><dd>{issue.comment_count}</dd></div>
      </dl>
      {issue.labels.length ? (
        <div className="issue-triage-detail-labels" aria-label="GitHub labels">
          {issue.labels.map((itemLabel) => <span className="badge issue-label-badge" key={itemLabel} style={githubLabelStyle(itemLabel, issue.label_colors)}>{itemLabel}</span>)}
        </div>
      ) : null}
      <div className="issue-triage-body">
        <h4>Issue body</h4>
        <p className="muted">External, untrusted Markdown rendered as text.</p>
        <pre>{detail.body || "(No description provided.)"}</pre>
      </div>
    </aside>
  );
}
