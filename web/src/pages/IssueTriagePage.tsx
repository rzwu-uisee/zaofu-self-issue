import type { CSSProperties, ReactNode } from "react";
import { useCallback, useEffect, useMemo, useState } from "react";
import { ArrowDown, ArrowUp, ChevronDown, ChevronUp, CircleCheck, ExternalLink, RefreshCw, Search, SlidersHorizontal, Star } from "lucide-react";
import { MarkdownText } from "../components/agent-session/MarkdownText";
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
  ISSUE_TRIAGE_MIRROR_POLL_INTERVAL_MS,
  githubMarkdownForDisplay,
  issueTriageSourceLabel,
} from "./issueTriageModel";

export type TriageTab = "github" | "gitlab" | "runtime";

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

function compactStat(value: number): string {
  return new Intl.NumberFormat(undefined, { notation: "compact", maximumFractionDigits: 1 }).format(value);
}

function githubAvatarUrl(login: string, supplied?: string): string {
  if (supplied?.startsWith("https://")) return supplied;
  return `https://github.com/${encodeURIComponent(login)}.png?size=40`;
}

function GithubMark({ size = 15 }: { size?: number }) {
  return (
    <svg aria-hidden="true" height={size} viewBox="0 0 16 16" width={size}>
      <path
        d="M8 0a8 8 0 0 0-2.53 15.59c.4.08.55-.17.55-.38v-1.49c-2.23.49-2.7-1.08-2.7-1.08-.36-.93-.89-1.17-.89-1.17-.73-.5.05-.49.05-.49.81.06 1.23.83 1.23.83.72 1.23 1.88.88 2.34.67.07-.52.28-.88.51-1.08-1.78-.2-3.65-.89-3.65-3.96 0-.88.31-1.59.83-2.15-.08-.2-.36-1.02.08-2.12 0 0 .68-.22 2.2.82a7.65 7.65 0 0 1 4 0c1.53-1.04 2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12.52.56.83 1.27.83 2.15 0 3.08-1.87 3.75-3.66 3.95.29.25.54.74.54 1.5v2.22c0 .21.15.46.55.38A8 8 0 0 0 8 0Z"
        fill="currentColor"
      />
    </svg>
  );
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
          className={tab === "github" ? "active" : ""}
          role="tab"
          type="button"
          aria-selected={tab === "github"}
          onClick={() => onTabChange("github")}
        >
          GITHUB ISSUE
        </button>
        <button
          className={tab === "gitlab" ? "active" : ""}
          role="tab"
          type="button"
          aria-selected={tab === "gitlab"}
          onClick={() => onTabChange("gitlab")}
        >
          GITLAB ISSUE
        </button>
        <button
          className={tab === "runtime" ? "active" : ""}
          role="tab"
          type="button"
          aria-selected={tab === "runtime"}
          onClick={() => onTabChange("runtime")}
        >
          RUNTIME INTERVENTIONS
        </button>
      </div>
      {tab === "github" ? <IssuesTab projectId={projectId} /> : tab === "gitlab" ? <GitlabPlaceholder /> : runtimeInterventions}
    </section>
  );
}

function GitlabPlaceholder() {
  return <section className="issue-triage-placeholder" aria-label="GitLab Issue" />;
}

function IssuesTab({ projectId }: { projectId: string }) {
  const [summary, setSummary] = useState<IssueTriageSummary | null>(null);
  const [page, setPage] = useState<IssueTriagePageData | null>(null);
  const [detail, setDetail] = useState<IssueTriageDetail | null>(null);
  const [query, setQuery] = useState("");
  const [appliedQuery, setAppliedQuery] = useState("");
  const [group, setGroup] = useState("");
  const [state, setState] = useState("");
  const [selectedLabels, setSelectedLabels] = useState<string[] | null>(null);
  const [selectedAuthors, setSelectedAuthors] = useState<string[] | null>(null);
  const [source, setSource] = useState("");
  const [orderBy, setOrderBy] = useState<"created" | "name">("created");
  const [orderDirection, setOrderDirection] = useState<"asc" | "desc">("desc");
  const [cursor, setCursor] = useState(0);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState("");

  const filters = useMemo(() => ({
    q: appliedQuery,
    group,
    state,
    label: selectedLabels?.length === 1 ? selectedLabels[0] : "",
    labels: selectedLabels === null ? "" : JSON.stringify(selectedLabels),
    author: selectedAuthors?.length === 1 ? selectedAuthors[0] : "",
    authors: selectedAuthors === null ? "" : JSON.stringify(selectedAuthors),
    source,
    order_by: orderBy,
    order_direction: orderDirection,
    cursor,
    limit: 50,
  }), [appliedQuery, cursor, group, orderBy, orderDirection, selectedAuthors, selectedLabels, source, state]);

  const labelColors = useMemo(() => {
    const merged = { ...(summary?.label_colors ?? {}) };
    for (const item of page?.items ?? []) Object.assign(merged, item.label_colors);
    return merged;
  }, [page?.items, summary?.label_colors]);

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
      await refreshIssueTriage(projectId, showSpinner);
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
      .catch((cause) => {
        if (active) setError(cause instanceof Error ? cause.message : String(cause));
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => { active = false; };
  }, [load]);

  useEffect(() => {
    const timer = window.setInterval(() => {
      if (document.visibilityState === "visible") void load().catch(() => undefined);
    }, ISSUE_TRIAGE_MIRROR_POLL_INTERVAL_MS);
    return () => window.clearInterval(timer);
  }, [load]);

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
        <p className="muted issue-triage-description">Read-only in ZaoFu. Edit labels, state, assignees, or content on GitHub.</p>
        <div className="button-row">
          {summary ? (
            <a className="issue-triage-repository-action" href={summary.repository_url} target="_blank" rel="noopener noreferrer">
              <span className="issue-triage-repository-label"><GithubMark /> View Repo</span>
              <span className="issue-triage-repository-stat" title={`${summary.sync.star_count ?? 0} GitHub stars`}>
                <Star aria-hidden="true" size={14} /> {compactStat(summary.sync.star_count ?? 0)}
              </span>
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
        <OrderFilter
          orderBy={orderBy}
          direction={orderDirection}
          onOrderBy={(value) => { setOrderBy(value); setCursor(0); setDetail(null); }}
          onDirection={(value) => { setOrderDirection(value); setCursor(0); setDetail(null); }}
        />
        <form onSubmit={(event) => { event.preventDefault(); setCursor(0); setAppliedQuery(query.trim()); }}>
          <Search aria-hidden="true" size={15} />
          <input aria-label="Search issues" placeholder="Search title, number, author, or label" value={query} onChange={(event) => setQuery(event.target.value)} />
        </form>
        <select aria-label="GitHub state" value={state} onChange={(event) => resetCursor(setState, event.target.value)}>
          <option value="">All states</option><option value="open">Open</option><option value="closed">Closed</option>
        </select>
        <LabelFilter
          colors={labelColors}
          counts={summary?.labels ?? {}}
          selected={selectedLabels}
          onChange={(next) => {
            setSelectedLabels(next);
            setCursor(0);
            setDetail(null);
          }}
        />
        <ContributorFilter
          counts={summary?.authors ?? {}}
          selected={selectedAuthors}
          onChange={(next) => { setSelectedAuthors(next); setCursor(0); setDetail(null); }}
        />
        <select aria-label="Source" value={source} onChange={(event) => resetCursor(setSource, event.target.value)}>
          <option value="">All sources</option><option value="self_issue">/issue</option><option value="github_web">GitHub</option>
        </select>
      </div>

      <div className={`issue-triage-content ${detail ? "with-detail" : ""}`}>
        <div className="issue-triage-list" aria-busy={loading}>
          {loading ? <p className="empty-text">Loading Issue mirror…</p> : null}
          {!loading && !page?.items.length ? <p className="empty-text">No matching GitHub Issues.</p> : null}
          {page?.items.map((item) => (
            <div
              aria-label={`Open Issue ${item.number}`}
              className={`issue-triage-row ${detail?.issue.number === item.number ? "selected" : ""}`}
              key={item.issue_key}
              role="button"
              tabIndex={0}
              onClick={() => void openDetail(item.number)}
              onKeyDown={(event) => {
                if (event.key === "Enter" || event.key === " ") {
                  event.preventDefault();
                  void openDetail(item.number);
                }
              }}
            >
              <span
                aria-label={`This issue is ${item.github_state}.`}
                className="issue-state-indicator issue-triage-tooltip"
                data-tooltip={`This issue is ${item.github_state}.`}
              >
                {item.github_state === "closed"
                  ? <CircleCheck aria-hidden="true" className="issue-state-check" size={16} />
                  : <span className="issue-state-dot" />}
              </span>
              <span className="issue-triage-row-main">
                <strong>{item.title}</strong>
                <span className="muted issue-triage-row-meta">
                  <span>#{item.number} ·</span>
                  <AuthorIdentity
                    login={item.author_login}
                    avatarUrl={item.author_avatar_url}
                    states={summary?.author_states?.[item.author_login]}
                  />
                  <span>· {issueTriageSourceLabel(item.source)} · updated {new Date(item.updated_at).toLocaleString()}</span>
                </span>
                <span className="issue-label-row">{item.labels.map((itemLabel) => <span className="badge issue-label-badge" key={itemLabel} style={githubLabelStyle(itemLabel, item.label_colors)}>{itemLabel}</span>)}</span>
              </span>
              <span
                className="issue-group-badge issue-triage-tooltip"
                data-tooltip={item.workflow
                  ? `Workflow: ${item.workflow.state}${item.workflow.task_id ? ` · ${item.workflow.task_id}` : ""}`
                  : `This issue is ${item.derived_group}.`}
              >{item.workflow?.state ?? item.derived_group}</span>
            </div>
          ))}
          <div className="issue-triage-pagination">
            <button className="icon-button" disabled={!page || page.cursor === 0} type="button" onClick={() => { setCursor(Math.max(0, cursor - 50)); setDetail(null); }}>Previous</button>
            <span className="muted">{page ? `${page.cursor + (page.items.length ? 1 : 0)}–${page.cursor + page.items.length} of ${page.total}` : "0 issues"}</span>
            <button className="icon-button" disabled={page?.next_cursor == null} type="button" onClick={() => { setCursor(page?.next_cursor ?? cursor); setDetail(null); }}>Next</button>
          </div>
        </div>
        {detail ? <IssueDetail projectId={projectId} detail={detail} authorStates={summary?.author_states ?? {}} onClose={() => setDetail(null)} /> : null}
      </div>
    </section>
  );
}

function AuthorIdentity({
  login,
  avatarUrl,
  states,
}: {
  login: string;
  avatarUrl?: string;
  states?: { open: number; closed: number };
}) {
  const profileUrl = `https://github.com/${encodeURIComponent(login)}`;
  return <span className="issue-author-identity" onClick={(event) => event.stopPropagation()}>
    <img
      className="issue-author-avatar"
      src={githubAvatarUrl(login, avatarUrl)}
      alt={`${login} avatar`}
      loading="lazy"
      referrerPolicy="no-referrer"
    />
    <span className="issue-author-login">{login}</span>
    <span className="issue-author-popover" role="tooltip">
      <span className="issue-author-popover-heading">
        <img src={githubAvatarUrl(login, avatarUrl)} alt="" loading="lazy" referrerPolicy="no-referrer" />
        <strong>{login}</strong>
      </span>
      <span className="issue-author-popover-stats">{states?.open ?? 0} open · {states?.closed ?? 0} closed issues</span>
      <a href={profileUrl} rel="noopener noreferrer" target="_blank" onClick={(event) => event.stopPropagation()}>
        <GithubMark /> View GitHub profile
      </a>
    </span>
  </span>;
}

function OrderFilter({
  orderBy,
  direction,
  onOrderBy,
  onDirection,
}: {
  orderBy: "created" | "name";
  direction: "asc" | "desc";
  onOrderBy: (value: "created" | "name") => void;
  onDirection: (value: "asc" | "desc") => void;
}) {
  const [open, setOpen] = useState(false);
  return (
    <div className="issue-order-filter" onBlur={(event) => {
      if (!event.currentTarget.contains(event.relatedTarget as Node | null)) setOpen(false);
    }}>
      <button aria-expanded={open} aria-label="Order issues" className="issue-order-filter-trigger" type="button" onClick={() => setOpen((value) => !value)}>
        <SlidersHorizontal aria-hidden="true" size={16} />
      </button>
      {open ? <div className="issue-order-filter-menu">
        <strong>Ordering</strong>
        <div className="issue-order-filter-controls">
          <div className="issue-order-direction" aria-label="Sort direction">
            <button aria-label="Ascending" className={direction === "asc" ? "active" : ""} type="button" onClick={() => onDirection("asc")}><ArrowUp size={15} /></button>
            <button aria-label="Descending" className={direction === "desc" ? "active" : ""} type="button" onClick={() => onDirection("desc")}><ArrowDown size={15} /></button>
          </div>
          <select aria-label="Order field" value={orderBy} onChange={(event) => onOrderBy(event.target.value as "created" | "name")}>
            <option value="created">Date created</option>
            <option value="name">Name</option>
          </select>
        </div>
      </div> : null}
    </div>
  );
}

function ContributorFilter({
  counts,
  selected,
  onChange,
}: {
  counts: Record<string, number>;
  selected: string[] | null;
  onChange: (authors: string[] | null) => void;
}) {
  const [open, setOpen] = useState(false);
  const [search, setSearch] = useState("");
  const options = useMemo(() => Object.keys(counts).sort((left, right) => left.localeCompare(right)), [counts]);
  const visible = options.filter((item) => item.toLocaleLowerCase().includes(search.trim().toLocaleLowerCase()));
  const active = selected === null ? options : selected.filter((item) => options.includes(item));
  const allSelected = selected === null || (options.length > 0 && active.length === options.length);
  const buttonLabel = selected === null ? "All contributors" : selected.length === 0 ? "No contributors" : `${selected.length} contributor${selected.length === 1 ? "" : "s"}`;
  const toggle = (item: string) => {
    const next = (selected === null || active.includes(item))
      ? active.filter((value) => value !== item)
      : [...active, item];
    onChange(next.length === options.length ? null : next);
  };
  return <div className="issue-label-filter issue-contributor-filter" onBlur={(event) => {
    if (!event.currentTarget.contains(event.relatedTarget as Node | null)) setOpen(false);
  }}>
    <button aria-expanded={open} aria-haspopup="menu" className="issue-label-filter-trigger" type="button" onClick={() => setOpen((value) => !value)}>
      <span>{buttonLabel}</span>{open ? <ChevronUp size={14} /> : <ChevronDown size={14} />}
    </button>
    {open ? <div className="issue-label-filter-menu" role="menu">
      <input aria-label="Filter contributors" placeholder="Filter contributors…" value={search} onChange={(event) => setSearch(event.target.value)} />
      <button
        aria-checked={allSelected}
        className="issue-label-filter-all"
        role="menuitemcheckbox"
        type="button"
        onClick={() => onChange(allSelected ? [] : null)}
      ><input checked={allSelected} readOnly tabIndex={-1} type="checkbox" /><strong>Select all</strong></button>
      <div className="issue-label-filter-options">
        {visible.map((item) => <button
          aria-checked={selected === null || active.includes(item)}
          className="issue-label-filter-option issue-contributor-filter-option"
          key={item}
          role="menuitemcheckbox"
          type="button"
          onClick={() => toggle(item)}
        >
          <input checked={selected === null || active.includes(item)} readOnly tabIndex={-1} type="checkbox" />
          <span className="issue-contributor-name"><img className="issue-author-avatar" src={githubAvatarUrl(item)} alt="" loading="lazy" />{item}</span>
          <span className="muted">{counts[item]}</span>
        </button>)}
      </div>
    </div> : null}
  </div>;
}

function SyncBanner({ summary, error }: { summary: IssueTriageSummary | null; error: string }) {
  const sync = summary?.sync;
  const stale = Boolean(error || !sync || sync.status === "never" || sync.status === "failed" || sync.status === "rate_limited");
  const status = stale
    ? "GitHub mirror disconnected"
    : sync?.status === "syncing"
      ? "GitHub mirror syncing"
      : "GitHub mirror is current";
  return (
    <div className={`issue-triage-sync ${stale ? "stale" : ""}`}>
      <span className={`issue-triage-sync-dot ${stale ? "stale" : sync?.status === "syncing" ? "syncing" : "current"}`} title={status} aria-label={status} />
      <span className="muted">{sync?.last_success_at ? `Last synced ${new Date(sync.last_success_at).toLocaleString()}` : "Not synchronized yet"}</span>
    </div>
  );
}

function Metric({ label, value, active, onClick }: { label: string; value: number; active: boolean; onClick: () => void }) {
  return <button className={`issue-triage-metric ${active ? "active" : ""}`} type="button" onClick={onClick}><strong>{value}</strong><span>{label}</span></button>;
}

function LabelFilter({
  colors,
  counts,
  selected,
  onChange,
}: {
  colors: Record<string, string>;
  counts: Record<string, number>;
  selected: string[] | null;
  onChange: (labels: string[] | null) => void;
}) {
  const [open, setOpen] = useState(false);
  const [search, setSearch] = useState("");
  const options = useMemo(() => Object.keys(counts).sort((left, right) => left.localeCompare(right)), [counts]);
  const visible = options.filter((item) => item.toLocaleLowerCase().includes(search.trim().toLocaleLowerCase()));
  const activeLabels = selected === null ? options : selected.filter((item) => options.includes(item));
  const allSelected = selected === null || (options.length > 0 && activeLabels.length === options.length);
  const buttonLabel = selected === null
    ? "All labels"
    : selected.length === 0
      ? "No labels"
      : `${selected.length} label${selected.length === 1 ? "" : "s"}`;

  const toggleLabel = (item: string) => {
    const checked = selected === null || activeLabels.includes(item);
    const next = checked
      ? activeLabels.filter((value) => value !== item)
      : [...activeLabels, item];
    onChange(next.length === options.length ? null : next);
  };

  return (
    <div
      className="issue-label-filter"
      onBlur={(event) => {
        if (!event.currentTarget.contains(event.relatedTarget as Node | null)) setOpen(false);
      }}
    >
      <button
        aria-expanded={open}
        aria-haspopup="menu"
        className="issue-label-filter-trigger"
        type="button"
        onClick={() => setOpen((value) => !value)}
      >
        <span>{buttonLabel}</span>
        {open ? <ChevronUp aria-hidden="true" size={14} /> : <ChevronDown aria-hidden="true" size={14} />}
      </button>
      {open ? (
        <div className="issue-label-filter-menu" role="menu">
          <input
            aria-label="Filter labels"
            placeholder="Filter labels…"
            value={search}
            onChange={(event) => setSearch(event.target.value)}
          />
          <button
            aria-checked={allSelected}
            className="issue-label-filter-all"
            role="menuitemcheckbox"
            type="button"
            onClick={() => onChange(allSelected ? [] : null)}
          >
            <input
              checked={allSelected}
              readOnly
              tabIndex={-1}
              type="checkbox"
            />
            <strong>Select all</strong>
          </button>
          <div className="issue-label-filter-options">
            {visible.map((item) => (
              <button
                aria-checked={selected === null || activeLabels.includes(item)}
                className="issue-label-filter-option"
                key={item}
                role="menuitemcheckbox"
                type="button"
                onClick={() => toggleLabel(item)}
              >
                <input
                  checked={selected === null || activeLabels.includes(item)}
                  readOnly
                  tabIndex={-1}
                  type="checkbox"
                />
                <span className="badge issue-label-badge" style={githubLabelStyle(item, colors)}>{item}</span>
                <span className="muted">{counts[item]}</span>
              </button>
            ))}
            {!visible.length ? <span className="muted issue-label-filter-empty">No matching labels</span> : null}
          </div>
        </div>
      ) : null}
    </div>
  );
}

function IssueDetail({
  projectId,
  detail,
  authorStates,
  onClose,
}: {
  projectId: string;
  detail: IssueTriageDetail;
  authorStates: Record<string, { open: number; closed: number }>;
  onClose: () => void;
}) {
  const { issue } = detail;
  return (
    <aside
      className="issue-triage-detail"
      onClick={(event) => {
        if (!(event.target instanceof Element)) return;
        if (event.target.closest('button[title="Download image"]')) {
          event.preventDefault();
          return;
        }
        const image = event.target.closest('img[data-streamdown="image"]');
        const link = image?.closest("a");
        if (link?.href) {
          event.preventDefault();
          const opened = window.open(link.href, "_blank");
          if (opened) opened.opener = null;
        }
      }}
    >
      <header className="issue-triage-detail-header">
        <span className="muted">GitHub Issue #{issue.number}</span>
        <div className="button-row issue-triage-detail-actions">
          <a className="icon-button primary" href={issue.html_url} target="_blank" rel="noopener noreferrer">Open on GitHub <ExternalLink aria-hidden="true" size={14} /></a>
          <a className="icon-button" href={issue.html_url} target="_blank" rel="noopener noreferrer">Edit on GitHub</a>
          <button className="icon-button" type="button" onClick={onClose}>Close</button>
        </div>
        <h3>{issue.title}</h3>
      </header>
      <dl className="issue-triage-meta">
        <div><dt>State</dt><dd>{issue.github_state}</dd></div>
        <div><dt>Group</dt><dd>{issue.derived_group}</dd></div>
        <div><dt>Workflow</dt><dd>{issue.workflow?.state ?? "mirrored"}</dd></div>
        <div><dt>Task</dt><dd>{issue.workflow?.task_id || "None"}</dd></div>
        <div><dt>Reporter</dt><dd><IssueDetailIdentity login={issue.author_login} avatarUrl={issue.author_avatar_url} /></dd></div>
        <div><dt>Assignees</dt><dd className="issue-detail-identities">{issue.assignees.length
          ? issue.assignees.map((login) => <IssueDetailIdentity avatarUrl={issue.assignee_avatar_urls?.[login]} key={login} login={login} />)
          : "None"}</dd></div>
        <div><dt>Milestone</dt><dd>{issue.milestone || "None"}</dd></div>
        <div><dt>Comments</dt><dd>{issue.comment_count}</dd></div>
        <div className="issue-triage-label-field"><dt>Labels</dt><dd>{issue.labels.length ? (
          <span className="issue-triage-detail-labels" aria-label="GitHub labels">
            {issue.labels.map((itemLabel) => <span className="badge issue-label-badge" key={itemLabel} style={githubLabelStyle(itemLabel, issue.label_colors)}>{itemLabel}</span>)}
          </span>
        ) : "None"}</dd></div>
      </dl>
      <div className="issue-triage-body">
        <div className="issue-triage-section-heading"><h4>Issue body</h4><span className="muted">GitHub Markdown preview</span></div>
        <div className="issue-triage-markdown">
          <MarkdownText content={githubMarkdownForDisplay(detail.body || "(No description provided.)", projectId)} />
        </div>
      </div>
      <section className="issue-triage-comments">
        <h4>Comments ({detail.comments?.length ?? issue.comment_count})</h4>
        {detail.comments?.map((comment) => <article className="issue-triage-comment" key={comment.id}>
          <header>
            <AuthorIdentity
              login={comment.author_login}
              avatarUrl={comment.author_avatar_url}
              states={authorStates[comment.author_login]}
            />
            <a href={comment.html_url} rel="noopener noreferrer" target="_blank">
              {new Date(comment.created_at).toLocaleString()}
            </a>
          </header>
          <div className="issue-triage-markdown">
            <MarkdownText content={githubMarkdownForDisplay(comment.body || "(Empty comment.)", projectId)} />
          </div>
        </article>)}
        {!detail.comments?.length ? <p className="muted">No comments.</p> : null}
      </section>
    </aside>
  );
}

function IssueDetailIdentity({ login, avatarUrl }: { login: string; avatarUrl?: string }) {
  return <a
    className="issue-detail-identity"
    href={`https://github.com/${encodeURIComponent(login)}`}
    rel="noopener noreferrer"
    target="_blank"
  >
    <img src={githubAvatarUrl(login, avatarUrl)} alt={`${login} avatar`} loading="lazy" referrerPolicy="no-referrer" />
    <span>{login}</span>
  </a>;
}
