import type { DeliveryTrace } from "../src/api/types.js";
import { canonicalTraceIdForTask, isRegressionCaptureEligible } from "../src/components/delivery-trace/runTraceRefs.js";

function assert(condition: unknown, message: string): void {
  if (!condition) throw new Error(message);
}

function traceWithRefs(canonical_trace_refs: DeliveryTrace["canonical_trace_refs"]): DeliveryTrace {
  return { canonical_trace_refs } as DeliveryTrace;
}

assert(
  canonicalTraceIdForTask(traceWithRefs(["trace-delivery-level"]), "TASK-1") === "",
  "task context must not accept an unscoped canonical trace ref",
);
assert(
  canonicalTraceIdForTask(traceWithRefs([{ trace_id: "trace-other", task_id: "TASK-2" }]), "TASK-1") === "",
  "task context must not fall back to another task's trace",
);
assert(
  canonicalTraceIdForTask(traceWithRefs([{ trace_id: "trace-unverified", task_id: "TASK-1" }]), "TASK-1") === "",
  "task context must require server-verified trace membership",
);
assert(
  canonicalTraceIdForTask(traceWithRefs([{ trace_id: "trace-task", task_id: "TASK-1", membership: "trace-v2-source-event" }]), "TASK-1") === "trace-task",
  "an exact task ref should open in Trace",
);
assert(
  canonicalTraceIdForTask(traceWithRefs([{ trace_id: "trace-shared", task_ids: ["TASK-1", "TASK-2"], membership: "trace-v2-source-event" }]), "TASK-1") === "trace-shared",
  "an explicit task_ids membership should open in Trace",
);

const eligibilityTrace = {
  task_lifecycle: {
    schema_version: "task-lifecycle.v2",
    tasks: {
      "TASK-FAILED": {
        state_history: [{ state: "running" }],
        tries: [{ try: 1, outcome: "failed", gate_results: [] }],
      },
      "TASK-RUNNING": {
        state_history: [{ state: "running" }],
        tries: [{ try: 1, outcome: "in_flight", gate_results: [] }],
      },
      "TASK-CANONICAL-DONE": {
        state_history: [{ state: "running" }],
        tries: [{ try: 1, outcome: "in_flight", gate_results: [] }],
      },
      "TASK-CANONICAL-FAILED": {
        state_history: [{ state: "running" }],
        tries: [{ try: 1, outcome: "in_flight", gate_results: [] }],
      },
    },
    task_statuses: {
      "TASK-CANONICAL-DONE": "done",
      "TASK-CANONICAL-FAILED": "failed",
      "TASK-FAILED": "done",
      "TASK-OMITTED-FAILED": "failed",
    },
    tasks_truncated: true,
    task_statuses_truncated: true,
  },
  execution_graph: {
    nodes: [
      { task_id: "TASK-CANONICAL-DONE", actual: { status: "failed" } },
      { task_id: "TASK-CANONICAL-FAILED", actual: { status: "done" } },
      { task_id: "TASK-STATUS-OMITTED", actual: { status: "failed" } },
    ],
  },
} as unknown as DeliveryTrace;
assert(
  isRegressionCaptureEligible(eligibilityTrace, "TASK-FAILED"),
  "a real failed attempt remains eligible after the canonical task reaches done",
);
assert(!isRegressionCaptureEligible(eligibilityTrace, "TASK-RUNNING"), "healthy in-flight task must not allow capture");
assert(
  isRegressionCaptureEligible(eligibilityTrace, "TASK-OMITTED-FAILED"),
  "failed canonical status remains capture eligible when lifecycle detail is omitted",
);
assert(
  !isRegressionCaptureEligible(eligibilityTrace, "TASK-CANONICAL-DONE"),
  "a stale failed execution node must not override canonical done without a regression signal",
);
assert(
  isRegressionCaptureEligible(eligibilityTrace, "TASK-CANONICAL-FAILED"),
  "canonical failed status must override a stale healthy execution node",
);
assert(
  !isRegressionCaptureEligible(eligibilityTrace, "TASK-STATUS-OMITTED"),
  "an omitted canonical status must fail closed instead of trusting a stale execution node",
);

console.log("run regression action tests passed");
