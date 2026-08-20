import type { DeliveryTrace } from "../src/api/types";
import {
  deliveryWorkGoalOptions,
  workClosureStatusLabel,
} from "../src/components/delivery-trace/deliveryWorkGoalModel.js";

function assert(condition: unknown, message: string): void {
  if (!condition) throw new Error(message);
}

function equal(actual: unknown, expected: unknown, message: string): void {
  assert(actual === expected, `${message}: expected ${String(expected)}, got ${String(actual)}`);
}

const trace = {
  feature_id: "F-1",
  goal_coverage_graph: {
    identity: { goal_id: "GOAL-A" },
    nodes: [
      { node_id: "goal:GOAL-A", kind: "goal", goal_id: "GOAL-A", title: "Goal A", status: "open" },
      {
        node_id: "node-ref:sha256:opaque",
        node_id_opaque: true,
        kind: "goal",
        goal_id: "goal-ref:sha256:opaque",
        goal_id_opaque: true,
        title: "Opaque Goal",
      },
      { node_id: "claim:A-1", kind: "goal_claim", goal_claim_id: "A-1", title: "A one" },
      { node_id: "claim:A-2", kind: "goal_claim", goal_claim_id: "A-2", title: "A two" },
      { node_id: "claim:B-1", kind: "goal_claim", goal_claim_id: "B-1", title: "B one" },
    ],
    edges: [
      { from: "goal:GOAL-A", to: "claim:A-1", kind: "has_claim" },
      { from: "goal:GOAL-A", to: "claim:A-2", kind: "has_claim" },
      { from: "node-ref:sha256:opaque", to: "claim:B-1", kind: "has_claim" },
    ],
  },
} as unknown as DeliveryTrace;

const goals = deliveryWorkGoalOptions(trace);
equal(goals.length, 2, "all visible Goal summaries are returned");
equal(goals[0]?.goalId, "GOAL-A", "exact canonical Goal ID is retained");
equal(goals[0]?.claimCount, 2, "claim count follows Goal topology");
equal(goals[0]?.expandable, true, "exact Goal can be expanded");
equal(goals[1]?.claimCount, 1, "opaque Goal still has a useful summary");
equal(goals[1]?.expandable, false, "opaque Goal handle cannot trigger a canonical request");
assert(
  goals[1]?.reason.includes("canonical Goal ID was omitted"),
  "opaque Goal explains why expansion is unavailable",
);

const identityOnly = deliveryWorkGoalOptions({
  feature_id: "F-1",
  goal_coverage_graph: {
    identity: { goal_id: "GOAL-FALLBACK" },
    nodes: [{ node_id: "claim:only", kind: "goal_claim", goal_claim_id: "only" }],
    edges: [],
  },
} as unknown as DeliveryTrace);
equal(identityOnly.length, 1, "identity-only summary remains visible");
equal(identityOnly[0]?.expandable, false, "identity-only summary fails closed");
equal(workClosureStatusLabel("unknown"), null, "missing closure verdict stays out of the normal UI");
equal(workClosureStatusLabel("rejected"), "rejected", "recorded closure verdict is preserved");
equal(workClosureStatusLabel(undefined), "status unavailable", "malformed projection is not called unevaluated");
equal(identityOnly[0]?.statusLabel, null, "picker omits an unevaluated status badge");

console.log("deliveryWorkGoalModel tests passed");
