import type { GoalCoverageGraph } from "../src/api/types";
import {
  filterClaims,
  hasUnavailableTaskDetails,
  missingTaskDetailCount,
  preferredClaimId,
  statusTone,
  taskNodesById,
} from "../src/components/goal-coverage/goalCoverageModel.js";

function assert(condition: unknown, message: string): void {
  if (!condition) throw new Error(message);
}

function equal(actual: unknown, expected: unknown, message: string): void {
  assert(actual === expected, `${message}: expected ${String(expected)}, got ${String(actual)}`);
}

function deepEqual(actual: unknown, expected: unknown, message: string): void {
  equal(JSON.stringify(actual), JSON.stringify(expected), message);
}

const graph: GoalCoverageGraph = {
  schema_version: "goal-coverage-graph.v1",
  coverage_mode: "explicit",
  identity: { goal_id: "GOAL-1", task_map_generation: "GEN-2" },
  currentness: { is_current_generation: true },
  summary: {
    mandatory_claims: 2,
    planned_claims: 1,
    claims_with_current_results: 1,
    closed_claims: 1,
    open_gaps: 1,
  },
  nodes: [
    { node_id: "goal:GOAL-1", kind: "goal", title: "Ship auth" },
    {
      node_id: "claim:CLAIM-A", kind: "goal_claim", goal_claim_id: "CLAIM-A",
      title: "Authentication is safe", plan_coverage: "covered", execution: "done",
      task_verification: "passed", closure: "closed", task_ids: ["TASK-A"],
    },
    {
      node_id: "claim:CLAIM-B", kind: "goal_claim", goal_claim_id: "CLAIM-B",
      title: "Replay is deterministic", plan_coverage: "uncovered", execution: "pending",
      task_verification: "unverified", closure: "open", task_ids: [],
    },
    { node_id: "task:TASK-A", kind: "task", task_id: "TASK-A", title: "Implement auth" },
  ],
  edges: [],
  diagnostics: [],
};

equal(preferredClaimId(graph, ""), "CLAIM-B", "uncovered claim is selected first");
equal(preferredClaimId(graph, "CLAIM-A"), "CLAIM-A", "current selection is stable");
deepEqual(filterClaims(graph, "implement auth").map((claim) => claim.goal_claim_id), ["CLAIM-A"], "task search");
deepEqual(filterClaims(graph, "replay").map((claim) => claim.goal_claim_id), ["CLAIM-B"], "claim search");
equal(taskNodesById(graph).get("TASK-A")?.title, "Implement auth", "task lookup");
const truncatedGraph: GoalCoverageGraph = {
  ...graph,
  nodes_truncated: true,
  nodes: graph.nodes.filter((node) => node.task_id !== "TASK-A"),
};
const truncatedClaim = truncatedGraph.nodes.find((node) => node.goal_claim_id === "CLAIM-A")!;
assert(
  hasUnavailableTaskDetails(truncatedGraph, truncatedClaim as never, taskNodesById(truncatedGraph)),
  "a retained claim with an omitted task node must report unavailable details",
);
assert(
  !hasUnavailableTaskDetails(graph, truncatedClaim as never, taskNodesById(graph)),
  "a complete graph must not report unavailable details",
);
const largeClaim = {
  ...truncatedClaim,
  task_ids: Array.from({ length: 16 }, (_, index) => `TASK-${index}`),
  task_details: { total: 50, included: 40, missing_count: 10 },
};
const retainedLargeTasks = new Map(
  largeClaim.task_ids.map((taskId) => [taskId, { node_id: `task:${taskId}`, kind: "task", task_id: taskId, title: taskId }]),
);
equal(
  missingTaskDetailCount({ ...graph, nodes_truncated: true }, largeClaim as never, retainedLargeTasks as never),
  34,
  "resolved task nodes, not the separately capped included count, determine omitted owner details",
);
equal(statusTone("closed"), "ok", "closed tone");
equal(statusTone("uncovered"), "err", "uncovered tone");
equal(statusTone("stale"), "warn", "stale tone");
equal(statusTone("in_progress"), "info", "in progress tone");

console.log("goalCoverageModel tests passed");
