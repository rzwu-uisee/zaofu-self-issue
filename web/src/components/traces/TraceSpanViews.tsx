import { useMemo, type CSSProperties } from "react";

import type { TraceLifecycleSpan } from "../../api/types";
import { formatDuration, traceStatus } from "./traceModel";
import {
  buildTraceSpanRows,
  buildTraceWaterfallRows,
  spanDurationSeconds,
} from "./traceSpanModel";

export type TraceSpanView = "tree" | "waterfall";

export function TraceSpanSurface({
  onSelect,
  selectedSpanId,
  spans,
  view,
}: {
  onSelect: (spanId: string) => void;
  selectedSpanId: string;
  spans: TraceLifecycleSpan[];
  view: TraceSpanView;
}) {
  return view === "tree" ? (
    <TraceSpanTree onSelect={onSelect} selectedSpanId={selectedSpanId} spans={spans} />
  ) : (
    <TraceSpanWaterfall onSelect={onSelect} selectedSpanId={selectedSpanId} spans={spans} />
  );
}

function TraceSpanTree({
  onSelect,
  selectedSpanId,
  spans,
}: {
  onSelect: (spanId: string) => void;
  selectedSpanId: string;
  spans: TraceLifecycleSpan[];
}) {
  const rows = useMemo(() => buildTraceSpanRows(spans), [spans]);
  return (
    <ol aria-label="Span tree" className="trace-span-tree" role="tree">
      {rows.map(({ depth, orphaned, span }) => (
        <li aria-level={depth + 1} key={span.span_id} role="treeitem">
          <button
            aria-current={selectedSpanId === span.span_id ? "true" : undefined}
            className={selectedSpanId === span.span_id ? "selected" : ""}
            style={{ "--trace-depth": depth } as CSSProperties}
            type="button"
            onClick={() => onSelect(span.span_id)}
          >
            <span className="trace-span-branch" aria-hidden="true" />
            <SpanIdentity orphaned={orphaned} span={span} />
            <SpanMetrics span={span} />
          </button>
        </li>
      ))}
    </ol>
  );
}

function TraceSpanWaterfall({
  onSelect,
  selectedSpanId,
  spans,
}: {
  onSelect: (spanId: string) => void;
  selectedSpanId: string;
  spans: TraceLifecycleSpan[];
}) {
  const rows = useMemo(() => buildTraceWaterfallRows(spans), [spans]);
  return (
    <div className="trace-waterfall" role="group" aria-label="Span waterfall">
      <div className="trace-waterfall-header">
        <span>Span</span>
        <span>Start → end</span>
      </div>
      {rows.map((row) => (
        <button
          aria-current={selectedSpanId === row.span.span_id ? "true" : undefined}
          className={`trace-waterfall-row${selectedSpanId === row.span.span_id ? " selected" : ""}`}
          key={row.span.span_id}
          type="button"
          onClick={() => onSelect(row.span.span_id)}
        >
          <span className="trace-waterfall-label">
            <span
              className="trace-waterfall-name"
              style={{ "--trace-depth": row.depth } as CSSProperties}
            >
              {row.span.name || row.span.span_id}
            </span>
            <span className="muted">{row.span.kind} · {formatDuration(spanDurationSeconds(row.span))}</span>
          </span>
          <span className="trace-waterfall-track">
            {row.timingKnown ? (
              <span
                aria-label={`${row.span.name} timing`}
                className={`trace-waterfall-bar trace-waterfall-bar-${traceStatus({ status: row.span.status, last_type: "" })}`}
                style={{ left: `${row.offsetPercent}%`, width: `${row.widthPercent}%` }}
              />
            ) : (
              <span className="trace-waterfall-unknown">Timing unavailable</span>
            )}
          </span>
        </button>
      ))}
    </div>
  );
}

function SpanIdentity({ orphaned, span }: { orphaned: boolean; span: TraceLifecycleSpan }) {
  return (
    <span className="trace-span-identity">
      <strong>{span.name || span.span_id}</strong>
      <span className="muted">
        {span.kind} · {span.source}{orphaned ? " · parent unavailable" : ""}
      </span>
    </span>
  );
}

function SpanMetrics({ span }: { span: TraceLifecycleSpan }) {
  const status = traceStatus({ status: span.status, last_type: "" });
  return (
    <span className="trace-span-metrics">
      <span className={`trace-status trace-status-${status}`}>{span.status}</span>
      <span className="mono muted">{formatDuration(spanDurationSeconds(span))}</span>
    </span>
  );
}
