import type { DeliveryTrace } from "../src/api/types";
import {
  buildDeliveryWorkModel,
  latestTry,
  resultStatus,
} from "../src/components/delivery-trace/deliveryWorkModel.js";

function assert(condition: unknown, message: string): void {
  if (!condition) throw new Error(message);
}

function equal(actual: unknown, expected: unknown, message: string): void {
  assert(actual === expected, `${message}: expected ${String(expected)}, got ${String(actual)}`);
}

function deepEqual(actual: unknown, expected: unknown, message: string): void {
  equal(JSON.stringify(actual), JSON.stringify(expected), message);
}

const trace = {
  feature_id: "GOAL-1",
  status: "in_progress",
  execution_graph: {
    nodes: [
      {
        task_id: "TASK-SHARED",
        title: "Implement shared boundary",
        planned: { blocked_by: [] },
        actual: { status: "done", assigned_to: "impl-1", evidence_events: ["evt-shared"] },
        drift: [],
      },
      {
        task_id: "TASK-B",
        title: "Implement replay",
        planned: { blocked_by: ["TASK-SHARED"] },
        actual: { status: "in_progress", assigned_to: "impl-2", evidence_events: [] },
        drift: [],
      },
      {
        task_id: "TASK-UNMAPPED",
        title: "Legacy cleanup",
        planned: { blocked_by: [] },
        actual: { status: "pending", assigned_to: "impl-3", evidence_events: [] },
        drift: [],
      },
    ],
    edges: [],
    waves: [],
  },
  task_lifecycle: {
    schema_version: "task-lifecycle.v1",
    tasks: {
      "TASK-SHARED": {
        state_history: [],
        tries: [
          { try: 1, outcome: "failed", dispatch_id: "d1", gate_results: [{ type: "verify", passed: false }] },
          { try: 2, outcome: "done", dispatch_id: "d2", gate_results: [{ type: "verify", passed: true }] },
        ],
      },
    },
  },
  goal_coverage_graph: {
    schema_version: "goal-coverage-graph.v1",
    coverage_mode: "explicit",
    identity: { goal_id: "GOAL-1", task_map_generation: "GEN-1" },
    currentness: { is_current_generation: true },
    summary: {
      mandatory_claims: 2,
      planned_claims: 2,
      claims_with_current_results: 1,
      closed_claims: 1,
      open_gaps: 1,
    },
    nodes: [
      { node_id: "goal:GOAL-1", kind: "goal", title: "Ship shared boundary", status: "rejected" },
      {
        node_id: "claim:CLAIM-A", kind: "goal_claim", goal_claim_id: "CLAIM-A",
        title: "Authorization is safe", plan_coverage: "covered", execution: "done",
        task_verification: "passed", closure: "closed", task_ids: ["TASK-SHARED"],
      },
      {
        node_id: "claim:CLAIM-B", kind: "goal_claim", goal_claim_id: "CLAIM-B",
        title: "Replay is deterministic", plan_coverage: "covered", execution: "running",
        task_verification: "unverified", closure: "open", task_ids: ["TASK-SHARED", "TASK-B"],
      },
      {
        node_id: "task:TASK-SHARED", kind: "task", task_id: "TASK-SHARED",
        title: "Implement shared boundary", status: "done", goal_claim_ids: ["CLAIM-A", "CLAIM-B"],
      },
      {
        node_id: "task:TASK-B", kind: "task", task_id: "TASK-B",
        title: "Implement replay", status: "in_progress", goal_claim_ids: ["CLAIM-B"],
      },
      {
        node_id: "result:shared", kind: "verification_result", task_id: "TASK-SHARED",
        title: "Shared boundary verified", status: "passed", result_ref: "artifact://shared",
        evidence_refs: ["artifact://proof"], current: true,
      },
    ],
    edges: [],
    diagnostics: [],
  },
} as unknown as DeliveryTrace;

const model = buildDeliveryWorkModel(trace);
const claimA = model.claims.find((claim) => claim.claim.goal_claim_id === "CLAIM-A")!;
const claimB = model.claims.find((claim) => claim.claim.goal_claim_id === "CLAIM-B")!;
const shared = model.tasks.find((task) => task.taskId === "TASK-SHARED")!;

equal(model.tasks.length, 3, "all canonical execution tasks are retained");
deepEqual(claimA.tasks.map((task) => task.taskId), ["TASK-SHARED"], "shared task has one primary claim");
deepEqual(claimB.tasks.map((task) => task.taskId), ["TASK-B"], "second claim owns its primary task");
deepEqual(claimB.linkedTasks.map((task) => task.taskId), ["TASK-SHARED"], "shared task is a reference under another claim");
equal(model.claims.flatMap((claim) => claim.tasks).filter((task) => task.taskId === "TASK-SHARED").length, 1, "shared task renders canonically once");
deepEqual(model.unclaimedTasks.map((task) => task.taskId), ["TASK-UNMAPPED"], "unmapped execution work stays visible");
equal(latestTry(shared)?.try, 2, "latest try is selected by lifecycle order");
equal(resultStatus(shared), "passed", "current verification result drives task status");
deepEqual(shared.evidenceRefs, ["evt-shared", "artifact://proof"], "evidence references are deduplicated");
equal(model.summary.done, 1, "done tasks are summarized");
equal(model.summary.running, 1, "running tasks are summarized");
equal(model.summary.verified, 1, "current passed results are summarized");

const boundedTrace = structuredClone(trace) as DeliveryTrace;
boundedTrace.goal_coverage_graph!.nodes = boundedTrace.goal_coverage_graph!.nodes.filter((node) => (
  node.task_id !== "TASK-B"
));
const boundedClaim = boundedTrace.goal_coverage_graph!.nodes.find((node) => (
  node.goal_claim_id === "CLAIM-B"
));
if (boundedClaim) boundedClaim.task_ids = [];
const boundedExecution = boundedTrace.execution_graph.nodes.find((node) => node.task_id === "TASK-B");
if (boundedExecution) boundedExecution.goal_claim_ids = ["CLAIM-B"];
boundedTrace.execution_graph.task_count = 4;
boundedTrace.execution_graph.nodes_total = 4;
boundedTrace.execution_graph.nodes_omitted = 1;
boundedTrace.execution_graph.nodes_truncated = true;
boundedTrace.task_lifecycle = {
  schema_version: "task-lifecycle.v2",
  tasks: {},
  task_count: 1,
  tasks_included: 0,
  tasks_omitted: 1,
  tasks_truncated: true,
  task_statuses: { "TASK-B": "done" },
};
const boundedModel = buildDeliveryWorkModel(boundedTrace);
const boundedTask = boundedModel.tasks.find((task) => task.taskId === "TASK-B")!;
equal(boundedTask.primaryClaimId, "CLAIM-B", "execution claim identity prevents false unmapped work");
equal(boundedTask.status, "done", "canonical task status wins over stale execution status");
equal(boundedTask.lifecycleDetailsOmitted, true, "bounded lifecycle omission stays explicit");
equal(boundedModel.summary.total, 4, "canonical execution total survives bounded nodes");
equal(boundedModel.bounded.verificationPartial, true, "bounded verification is labeled partial");

const omittedClaimTrace = structuredClone(trace) as DeliveryTrace;
omittedClaimTrace.goal_coverage_graph!.nodes.push({
  node_id: "task:TASK-OMITTED-CLAIM",
  kind: "task",
  task_id: "TASK-OMITTED-CLAIM",
  title: "Mapped to an omitted claim",
  status: "pending",
  goal_claim_ids: ["CLAIM-NOT-IN-BOUNDED-GRAPH"],
});
omittedClaimTrace.goal_coverage_graph!.nodes.push({
  node_id: "task:TASK-OVER-CAP",
  kind: "task",
  task_id: "TASK-OVER-CAP",
  title: "True unclaimed task omitted by Work cap",
  status: "pending",
  goal_claim_ids: [],
});
const omittedClaimModel = buildDeliveryWorkModel(omittedClaimTrace);
assert(
  !omittedClaimModel.tasks.some((task) => task.taskId === "TASK-OMITTED-CLAIM"),
  "a task mapped only to an omitted claim is not relabeled as unmapped",
);
assert(
  !omittedClaimModel.tasks.some((task) => task.taskId === "TASK-OVER-CAP"),
  "a Goal task omitted by the Work node cap does not bypass execution visibility",
);
deepEqual(
  omittedClaimModel.unclaimedTasks.map((task) => task.taskId),
  ["TASK-UNMAPPED"],
  "truly unclaimed canonical work remains visible",
);

const claimTruncatedTrace = structuredClone(trace) as DeliveryTrace;
const truncatedClaim = claimTruncatedTrace.goal_coverage_graph!.nodes.find((node) => (
  node.goal_claim_id === "CLAIM-B"
));
if (truncatedClaim) truncatedClaim.task_details = {
  total: 5,
  included: 2,
  missing_count: 3,
};
const claimTruncatedModel = buildDeliveryWorkModel(claimTruncatedTrace);
const claimWithOmittedTasks = claimTruncatedModel.claims.find((claim) => (
  claim.claim.goal_claim_id === "CLAIM-B"
))!;
equal(claimWithOmittedTasks.taskTotal, 5, "claim keeps authoritative covering task total");
equal(claimWithOmittedTasks.taskDetailsOmitted, 3, "claim exposes bounded task detail omission");

console.log("deliveryWorkModel tests passed");
