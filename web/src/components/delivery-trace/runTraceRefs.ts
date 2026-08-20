import type { DeliveryTrace } from "../../api/types";

export function canonicalTraceIdForTask(trace: DeliveryTrace, taskId: string): string {
  const scoped = (trace.canonical_trace_refs ?? []).find((ref) => (
    typeof ref !== "string"
    && ref.membership === "trace-v2-source-event"
    && (ref.task_id === taskId || ref.task_ids?.includes(taskId))
  ));
  return typeof scoped === "string" ? "" : scoped?.trace_id?.trim() ?? "";
}

export function isRegressionCaptureEligible(trace: DeliveryTrace, taskId: string): boolean {
  const lifecycle = trace.task_lifecycle?.tasks?.[taskId];
  if (lifecycle?.state_history.some((item) => ["failed", "blocked"].includes(item.state))) return true;
  if (lifecycle?.tries.some((item) => (
    ["failed", "blocked"].includes(item.outcome)
    || Boolean(item.rework_kind)
    || item.gate_results.some((gate) => !gate.passed)
  ))) return true;
  const canonicalStatus = trace.task_lifecycle?.task_statuses?.[taskId] ?? "";
  if (canonicalStatus) return ["failed", "blocked"].includes(canonicalStatus);
  if (trace.task_lifecycle?.task_statuses_truncated) return false;
  const legacyNodeStatus = trace.execution_graph?.nodes.find((node) => node.task_id === taskId)?.actual.status ?? "";
  return ["failed", "blocked"].includes(legacyNodeStatus);
}
