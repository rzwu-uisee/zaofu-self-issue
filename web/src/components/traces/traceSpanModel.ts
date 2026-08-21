import type { TraceDetail, TraceLifecycleSpan, TraceSpanPage } from "../../api/types";

export type TraceViewerMode = "auto" | "spans" | "execution" | "events";
export type ResolvedTraceViewerMode = Exclude<TraceViewerMode, "auto">;

export interface TraceSpanRow {
  span: TraceLifecycleSpan;
  depth: number;
  orphaned: boolean;
}

export interface TraceWaterfallRow extends TraceSpanRow {
  offsetPercent: number;
  widthPercent: number;
  timingKnown: boolean;
}

export function resolveTraceViewerMode(
  requested: TraceViewerMode,
  spanLoading: boolean,
  detailLoading: boolean,
  spanPage: TraceSpanPage | null,
  detail: TraceDetail | null,
): ResolvedTraceViewerMode {
  if (requested !== "auto") return requested;
  if (spanLoading || detailLoading || spanPage?.items.length) return "spans";
  if (detail?.execution_route && !detail.execution_route.empty) return "execution";
  return "events";
}

export function buildTraceSpanRows(spans: TraceLifecycleSpan[]): TraceSpanRow[] {
  if (!spans.length) return [];
  const byId = new Map<string, TraceLifecycleSpan>();
  for (const span of spans) {
    if (!byId.has(span.span_id)) byId.set(span.span_id, span);
  }

  const children = new Map<string, TraceLifecycleSpan[]>();
  const roots: TraceLifecycleSpan[] = [];
  for (const span of byId.values()) {
    const parentId = span.parent_span_id;
    if (!parentId || parentId === span.span_id || !byId.has(parentId)) {
      roots.push(span);
      continue;
    }
    const siblings = children.get(parentId) ?? [];
    siblings.push(span);
    children.set(parentId, siblings);
  }

  roots.sort(compareSpans);
  for (const siblings of children.values()) siblings.sort(compareSpans);

  const rows: TraceSpanRow[] = [];
  const visited = new Set<string>();
  const visit = (span: TraceLifecycleSpan, depth: number) => {
    if (visited.has(span.span_id)) return;
    visited.add(span.span_id);
    rows.push({
      depth,
      orphaned: Boolean(span.parent_span_id && !byId.has(span.parent_span_id)),
      span,
    });
    for (const child of children.get(span.span_id) ?? []) visit(child, depth + 1);
  };

  for (const root of roots) visit(root, 0);
  // A malformed parent cycle has no root. Keep it visible without inventing a
  // hierarchy, and never recurse through an already visited node.
  for (const span of [...byId.values()].sort(compareSpans)) {
    if (!visited.has(span.span_id)) visit(span, 0);
  }
  return rows;
}

export function buildTraceWaterfallRows(spans: TraceLifecycleSpan[]): TraceWaterfallRow[] {
  const rows = buildTraceSpanRows(spans);
  let first = Number.POSITIVE_INFINITY;
  let last = Number.NEGATIVE_INFINITY;
  for (const { span } of rows) {
    const timing = spanTiming(span);
    if (!timing) continue;
    first = Math.min(first, timing.start);
    last = Math.max(last, timing.end);
  }
  const range = last - first;
  return rows.map((row) => {
    const timing = spanTiming(row.span);
    if (!timing || !Number.isFinite(range) || range < 0) {
      return { ...row, offsetPercent: 0, widthPercent: 0, timingKnown: false };
    }
    if (range === 0) {
      return { ...row, offsetPercent: 0, widthPercent: 0, timingKnown: true };
    }
    return {
      ...row,
      offsetPercent: ((timing.start - first) / range) * 100,
      widthPercent: ((timing.end - timing.start) / range) * 100,
      timingKnown: true,
    };
  });
}

export function spanDurationSeconds(span: TraceLifecycleSpan): number | null {
  if (typeof span.duration_seconds === "number" && Number.isFinite(span.duration_seconds)) {
    return Math.max(0, span.duration_seconds);
  }
  const timing = spanTiming(span);
  return timing ? (timing.end - timing.start) / 1000 : null;
}

function spanTiming(span: TraceLifecycleSpan): { start: number; end: number } | null {
  if (!span.started_at || !span.ended_at) return null;
  const start = Date.parse(span.started_at);
  const end = Date.parse(span.ended_at);
  if (!Number.isFinite(start) || !Number.isFinite(end) || end < start) return null;
  return { start, end };
}

function compareSpans(left: TraceLifecycleSpan, right: TraceLifecycleSpan): number {
  const leftStart = Date.parse(left.started_at ?? "");
  const rightStart = Date.parse(right.started_at ?? "");
  const byStart = (Number.isFinite(leftStart) ? leftStart : Number.MAX_SAFE_INTEGER)
    - (Number.isFinite(rightStart) ? rightStart : Number.MAX_SAFE_INTEGER);
  return byStart || left.span_id.localeCompare(right.span_id);
}
