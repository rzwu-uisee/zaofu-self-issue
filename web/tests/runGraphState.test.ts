import {
  lifecycleProjectionNotice,
  runChainProjectionNotice,
  runChainSearchMissLabel,
  taskProjectionDetailsOmitted,
  taskRgState,
} from "../src/components/delivery-trace/runGraphState.js";

function assert(condition: unknown, message: string): void {
  if (!condition) throw new Error(message);
}

const staleFailedLifecycle = {
  state_history: [{ state: "failed" }],
  tries: [{ try: 1, outcome: "failed", gate_results: [] }],
};
assert(
  taskRgState(staleFailedLifecycle, undefined, "done") === "done",
  "canonical terminal done must override stale failed lifecycle history",
);
const currentAttempt = {
  state_history: [{ state: "running" }],
  tries: [{ try: 1, outcome: "in_flight", gate_results: [] }],
};
assert(
  taskRgState(currentAttempt, undefined, "in_progress") === "running",
  "one in-flight attempt with canonical running status stays running",
);
assert(
  taskRgState(
    currentAttempt,
    { actual: { status: "failed" } } as never,
    "in_progress",
  ) === "running",
  "canonical status must override a stale execution-graph status",
);

const retriedAttempt = {
  state_history: [{ state: "running" }],
  tries: [
    { try: 1, outcome: "failed", gate_results: [] },
    { try: 2, outcome: "in_flight", gate_results: [] },
  ],
};
assert(
  taskRgState(retriedAttempt, undefined, "in_progress") === "retry",
  "an in-flight retry may refine a non-terminal canonical status",
);

const cappedLifecycle = {
  schema_version: "task-lifecycle.v2",
  task_count: 72,
  tasks_included: 8,
  tasks_omitted: 64,
  tasks_truncated: true,
  task_status_count: 72,
  task_statuses_included: 64,
  task_statuses_truncated: true,
  task_statuses: Object.fromEntries(Array.from({ length: 64 }, (_, index) => [`TASK-${index + 1}`, "done"])),
  tasks: {},
};
assert(
  lifecycleProjectionNotice(cappedLifecycle).includes("canonical status unavailable for 8 task(s)"),
  "a >64 task projection must not claim every canonical status remains visible",
);
const omitted = taskProjectionDetailsOmitted(cappedLifecycle, "TASK-72");
assert(omitted.details && omitted.status, "an uncapped task must report both lifecycle detail and status omitted");

const statusOnlyCap = {
  ...cappedLifecycle,
  task_count: 0,
  tasks_included: 0,
  tasks_omitted: 0,
  tasks_truncated: false,
};
assert(
  lifecycleProjectionNotice(statusOnlyCap) === "canonical status unavailable for 8 task(s).",
  "status truncation must surface even when lifecycle task details are not truncated",
);
const statusOnlyOmission = taskProjectionDetailsOmitted(statusOnlyCap, "TASK-72");
assert(!statusOnlyOmission.details && statusOnlyOmission.status, "status omission is independent of detail omission");
assert(
  taskRgState(staleFailedLifecycle, undefined, undefined, true) === "none",
  "stale lifecycle history must not impersonate a current status omitted by the cap",
);
assert(
  taskRgState(staleFailedLifecycle, { actual: { status: "done" } } as never, undefined, true) === "none",
  "a stale execution-graph status must not impersonate a canonical status omitted by the cap",
);

const detailsOnlyCap = {
  ...cappedLifecycle,
  task_statuses_truncated: false,
};
assert(
  lifecycleProjectionNotice(detailsOnlyCap)
    === "64 task lifecycle detail(s) omitted; retained canonical task statuses remain visible.",
  "detail truncation must report independently when canonical statuses remain available",
);

const cappedRunChain = {
  schema_version: "run-chain.v2",
  status: "in_progress",
  stages: [{
    stage: "implementation",
    status: "active",
    occurrences: 1,
    task_ids: Array.from({ length: 16 }, (_, index) => `TASK-${index + 1}`),
    task_ids_total: 40,
    task_ids_included: 16,
    task_ids_omitted: 24,
    task_ids_truncated: true,
  }],
  stage_count: 3,
  stages_total: 3,
  stages_included: 1,
  stages_omitted: 2,
  stages_truncated: true,
  task_ids_total: 40,
  task_ids_included: 16,
  task_ids_omitted: 24,
  task_ids_truncated: true,
};
assert(
  runChainProjectionNotice(cappedRunChain)
    === "2 stages omitted; 24 task relations omitted by the bounded projection.",
  "run-chain cap notice must report omitted stage and task-relation counts",
);
assert(
  runChainSearchMissLabel(cappedRunChain) === "not included in this bounded projection",
  "search misses in a truncated chain must not claim the task is outside the feature",
);
assert(
  runChainSearchMissLabel({
    ...cappedRunChain,
    stages_omitted: 0,
    stages_truncated: false,
    task_ids_omitted: 0,
    task_ids_truncated: false,
    stages: cappedRunChain.stages.map((stage) => ({
      ...stage,
      task_ids_omitted: 0,
      task_ids_truncated: false,
    })),
  }) === "not in this feature",
  "complete-chain search misses may still report feature absence",
);

console.log("run graph state tests passed");
