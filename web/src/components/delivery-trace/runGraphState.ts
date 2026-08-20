import type {
  DeliveryRunChain,
  DeliveryTaskLifecycle,
  DeliveryTaskLifecycleEntry,
  DeliveryTraceNode,
} from "../../api/types";

export type RunGraphState =
  | "none" | "ready" | "queued" | "running" | "done"
  | "failed" | "blocked" | "retry" | "superseded";

function omittedCount(
  omitted: number | undefined,
  total: number | undefined,
  included: number | undefined,
): number {
  if (omitted !== undefined) return Math.max(0, omitted);
  return Math.max(0, (total ?? 0) - (included ?? 0));
}

function runChainIsTruncated(chain: DeliveryRunChain | undefined): boolean {
  return Boolean(
    chain?.stages_truncated
    || chain?.task_ids_truncated
    || chain?.stages.some((stage) => stage.task_ids_truncated),
  );
}

export function runChainProjectionNotice(chain: DeliveryRunChain | undefined): string {
  if (!runChainIsTruncated(chain)) return "";
  const omittedStages = omittedCount(
    chain?.stages_omitted,
    chain?.stages_total ?? chain?.stage_count,
    chain?.stages_included ?? chain?.stages.length,
  );
  const omittedTaskRelations = omittedCount(
    chain?.task_ids_omitted,
    chain?.task_ids_total,
    chain?.task_ids_included,
  );
  const parts: string[] = [];
  if (chain?.stages_truncated) {
    parts.push(`${omittedStages} stage${omittedStages === 1 ? "" : "s"} omitted`);
  }
  if (chain?.task_ids_truncated || chain?.stages.some((stage) => stage.task_ids_truncated)) {
    parts.push(
      `${omittedTaskRelations} task relation${omittedTaskRelations === 1 ? "" : "s"} omitted`,
    );
  }
  return `${parts.join("; ")} by the bounded projection.`;
}

export function runChainSearchMissLabel(chain: DeliveryRunChain | undefined): string {
  return runChainIsTruncated(chain)
    ? "not included in this bounded projection"
    : "not in this feature";
}

const LIFECYCLE_TO_RUN_STATE: Record<string, RunGraphState> = {
  backlog: "none", ready: "ready", queued: "queued", running: "running",
  verify: "running", done: "done", failed: "failed", blocked: "blocked",
};

const CURRENT_STATUS_TO_RUN_STATE: Record<string, RunGraphState> = {
  backlog: "none", todo: "none", pending: "none", waiting: "none",
  ready: "ready", queued: "queued",
  done: "done", cancelled: "done",
  in_progress: "running", review: "running", test: "running", judge: "running", dispatched: "running",
  rework: "retry", blocked: "blocked", failed: "failed",
};

// Current canonical state wins over stale history. An active second attempt may
// refine a non-terminal current state to retry, but never override a terminal.
export function taskRgState(
  entry?: DeliveryTaskLifecycleEntry,
  node?: DeliveryTraceNode,
  canonicalStatus?: string,
  canonicalStatusOmitted = false,
): RunGraphState {
  if (node?.superseded) return "superseded";
  if (canonicalStatusOmitted) return "none";
  const history = entry?.state_history ?? [];
  const tries = entry?.tries ?? [];
  const last = history[history.length - 1]?.state ?? "";
  const currentStatus = canonicalStatus ?? node?.actual.status ?? "";
  const currentState = CURRENT_STATUS_TO_RUN_STATE[currentStatus];
  if (currentState && ["done", "failed", "blocked"].includes(currentState)) return currentState;
  if (tries.length >= 2 && tries[tries.length - 1]?.outcome === "in_flight") return "retry";
  if (currentStatus in CURRENT_STATUS_TO_RUN_STATE) return currentState;
  if (last && LIFECYCLE_TO_RUN_STATE[last]) return LIFECYCLE_TO_RUN_STATE[last];
  return "none";
}

export function lifecycleProjectionNotice(lifecycle: DeliveryTaskLifecycle | undefined): string {
  if (!lifecycle?.tasks_truncated && !lifecycle?.task_statuses_truncated) return "";
  const parts: string[] = [];
  if (lifecycle.tasks_truncated) {
    const omittedDetails = lifecycle.tasks_omitted
      ?? Math.max(0, (lifecycle.task_count ?? 0) - (lifecycle.tasks_included ?? 0));
    parts.push(`${omittedDetails} task lifecycle detail(s) omitted`);
  }
  if (lifecycle.task_statuses_truncated) {
    parts.push(`canonical status unavailable for ${Math.max(
        0,
        (lifecycle.task_status_count ?? lifecycle.task_count ?? 0)
          - (lifecycle.task_statuses_included ?? 0),
      )} task(s)`);
  } else if (lifecycle.tasks_truncated) {
    parts.push("retained canonical task statuses remain visible");
  }
  return `${parts.join("; ")}.`;
}

export function taskProjectionDetailsOmitted(
  lifecycle: DeliveryTaskLifecycle | undefined,
  taskId: string,
): { details: boolean; status: boolean } {
  const details = Boolean(lifecycle?.tasks_truncated && !lifecycle.tasks?.[taskId]);
  const hasCanonicalStatus = Object.prototype.hasOwnProperty.call(
    lifecycle?.task_statuses ?? {},
    taskId,
  );
  return {
    details,
    status: Boolean(lifecycle?.task_statuses_truncated && !hasCanonicalStatus),
  };
}
