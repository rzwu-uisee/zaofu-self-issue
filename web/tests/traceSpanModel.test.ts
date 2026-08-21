import type { TraceDetail, TraceLifecycleSpan, TraceSpanPage } from "../src/api/types.js";
import {
  buildTraceSpanRows,
  buildTraceWaterfallRows,
  resolveTraceViewerMode,
  spanDurationSeconds,
} from "../src/components/traces/traceSpanModel.js";

function assert(condition: unknown, message: string): void {
  if (!condition) throw new Error(message);
}

function span(
  spanId: string,
  parentSpanId: string | null,
  startedAt: string | null,
  endedAt: string | null,
): TraceLifecycleSpan {
  return {
    trace_id: "trace-demo",
    span_id: spanId,
    parent_span_id: parentSpanId,
    name: spanId,
    kind: "task.attempt",
    status: "completed",
    started_at: startedAt,
    ended_at: endedAt,
    duration_seconds: null,
    source: "events.jsonl",
    truth_class: "kernel.lifecycle",
    degraded: !startedAt || !endedAt,
    degradation_reason: !startedAt || !endedAt ? "unpaired lifecycle boundary" : null,
    source_event_ids: [`evt-${spanId}`],
  };
}

const root = span("root", null, "2026-08-19T08:00:00Z", "2026-08-19T08:00:10Z");
const child = span("child", "root", "2026-08-19T08:00:02.500Z", "2026-08-19T08:00:07.500Z");
const orphan = span("orphan", "missing", null, null);

const treeRows = buildTraceSpanRows([child, orphan, root]);
assert(treeRows.map((row) => row.span.span_id).join(",") === "root,child,orphan", "tree ordering must follow roots and children");
assert(treeRows[1]?.depth === 1, "child span must be nested under its proven parent");
assert(treeRows[2]?.orphaned === true, "missing parent must be exposed as degraded hierarchy");

const cycleA = span("cycle-a", "cycle-b", "2026-08-19T08:01:00Z", "2026-08-19T08:01:01Z");
const cycleB = span("cycle-b", "cycle-a", "2026-08-19T08:01:01Z", "2026-08-19T08:01:02Z");
const cycleRows = buildTraceSpanRows([cycleA, cycleB]);
assert(cycleRows.length === 2, "malformed parent cycles must stay bounded and visible exactly once");

const waterfallRows = buildTraceWaterfallRows([root, child, orphan]);
const rootWaterfall = waterfallRows.find((row) => row.span.span_id === "root");
const childWaterfall = waterfallRows.find((row) => row.span.span_id === "child");
const orphanWaterfall = waterfallRows.find((row) => row.span.span_id === "orphan");
assert(rootWaterfall?.offsetPercent === 0 && rootWaterfall.widthPercent === 100, "root must cover the measured time range");
assert(childWaterfall?.offsetPercent === 25 && childWaterfall.widthPercent === 50, "waterfall must preserve real start/end proportions");
assert(orphanWaterfall?.timingKnown === false && orphanWaterfall.widthPercent === 0, "unpaired spans must not receive a synthetic bar");
assert(spanDurationSeconds(child) === 5, "duration derives only from two valid boundaries");
assert(spanDurationSeconds(orphan) === null, "missing boundaries must remain unknown");

const spanPage = {
  items: [root],
} as TraceSpanPage;
const emptySpanPage = {
  ...spanPage,
  items: [],
  span_count: 0,
};
const routeDetail = {
  execution_route: { empty: false },
} as TraceDetail;
const eventDetail = {
  execution_route: { empty: true },
} as TraceDetail;

assert(resolveTraceViewerMode("auto", true, true, null, null) === "spans", "pending span coverage keeps the progressive span surface stable");
assert(resolveTraceViewerMode("auto", false, true, emptySpanPage, null) === "spans", "detail loading must not flash the event fallback");
assert(resolveTraceViewerMode("auto", false, false, spanPage, routeDetail) === "spans", "proven spans take priority");
assert(resolveTraceViewerMode("auto", false, false, emptySpanPage, routeDetail) === "execution", "empty span coverage falls back to execution");
assert(resolveTraceViewerMode("auto", false, false, emptySpanPage, eventDetail) === "events", "events are the final truthful fallback");
assert(resolveTraceViewerMode("events", true, true, spanPage, routeDetail) === "events", "explicit operator selection must not be overwritten");
