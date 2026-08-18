// Process/runtime diagnostics are intentionally distinct from event-derived
// audit logs. The API already redacts and bounds every row.
import { useEffect, useState } from "react";

import { getRuntimeLogs } from "../../api/client";
import type { RuntimeLogRow } from "../../api/types";

const LEVELS = ["DEBUG", "INFO", "WARN", "ERROR"] as const;

export function RuntimeLogsPanel({ projectId }: { projectId?: string }) {
  const [rows, setRows] = useState<RuntimeLogRow[]>([]);
  const [level, setLevel] = useState<string>("DEBUG");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    getRuntimeLogs(projectId, { limit: 200, level })
      .then((page) => {
        if (cancelled) return;
        setRows(page.rows);
        setError("");
      })
      .catch((err) => {
        if (!cancelled) setError(err instanceof Error ? err.message : String(err));
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [projectId, level]);

  return (
    <section className="subsection" data-testid="runtime-logs">
      <div className="inline-heading">
        <h3>Runtime Logs</h3>
        <span className="muted">redacted process diagnostics · newest first</span>
        <select value={level} onChange={(event) => setLevel(event.target.value)} aria-label="Minimum runtime log level">
          {LEVELS.map((value) => <option key={value} value={value}>{value}+</option>)}
        </select>
      </div>
      {error && <p className="error">{error}</p>}
      {loading && rows.length === 0 && <p className="muted">Loading…</p>}
      {!loading && rows.length === 0 && !error && <p className="muted">No runtime diagnostics yet.</p>}
      {rows.length > 0 && (
        <table className="data-table">
          <thead>
            <tr>
              <th>Time</th><th>Level</th><th>Component</th><th>Provider</th><th>Task</th><th>Message</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row, index) => (
              <tr key={`${row.timestamp}-${row.component}-${index}`}>
                <td>{row.timestamp.replace("T", " ").slice(0, 19)}</td>
                <td><span className={`badge badge-${row.level === "ERROR" ? "err" : row.level === "WARN" ? "warn" : "info"}`}>{row.level}</span></td>
                <td>{row.component}</td>
                <td>{row.provider || "-"}</td>
                <td>{row.task_id || "-"}</td>
                <td>{row.message}{row.failure_class ? ` (${row.failure_class})` : ""}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  );
}
