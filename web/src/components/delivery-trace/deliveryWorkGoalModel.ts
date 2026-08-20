import type { DeliveryTrace, GoalCoverageGraph, GoalCoverageNode } from "../../api/types";

export interface DeliveryWorkGoalOption {
  expandable: boolean;
  goalId: string;
  nodeId: string;
  reason: string;
  title: string;
  status: string;
  statusLabel: string | null;
  claimCount: number;
}

export function workClosureStatusLabel(status: string | undefined): string | null {
  if (status === "unknown") return null;
  return status || "status unavailable";
}

export function deliveryWorkGoalOptions(trace: DeliveryTrace): DeliveryWorkGoalOption[] {
  const graph = trace.goal_coverage_graph ?? null;
  if (!graph) return [];
  const goals = graph.nodes.filter((node) => node.kind === "goal");
  if (!goals.length && graph.identity.goal_id) {
    return [{
      expandable: false,
      goalId: graph.identity.goal_id,
      nodeId: `goal:${graph.identity.goal_id}`,
      reason: "An exact Goal ID is not available in this bounded summary.",
      title: graph.identity.goal_id,
      status: "unknown",
      statusLabel: workClosureStatusLabel("unknown"),
      claimCount: claimNodes(graph).length,
    }];
  }
  const seen = new Set<string>();
  return goals.flatMap((goal) => {
    const goalId = goal.goal_id || graph.identity.goal_id || goal.node_id;
    if (!goalId || seen.has(goalId)) return [];
    seen.add(goalId);
    return [{
      expandable: !goal.goal_id_opaque && Boolean(goal.goal_id),
      goalId,
      nodeId: goal.node_id,
      reason: goal.goal_id_opaque
        ? "The canonical Goal ID was omitted from this bounded summary."
        : goal.goal_id ? "" : "An exact Goal ID is unavailable.",
      title: goal.title || goalId,
      status: goal.status || "unknown",
      statusLabel: workClosureStatusLabel(goal.status),
      claimCount: claimCountForGoal(graph, goal, goals.length),
    }];
  });
}

function claimCountForGoal(
  graph: GoalCoverageGraph,
  goal: GoalCoverageNode,
  goalCount: number,
): number {
  const nodeById = new Map(graph.nodes.map((node) => [node.node_id, node]));
  const linked = new Set<string>();
  for (const edge of graph.edges) {
    const candidate = edge.from === goal.node_id
      ? edge.to
      : edge.to === goal.node_id ? edge.from : "";
    if (candidate && nodeById.get(candidate)?.kind === "goal_claim") linked.add(candidate);
  }
  return linked.size || (goalCount === 1 ? claimNodes(graph).length : 0);
}

function claimNodes(graph: GoalCoverageGraph): GoalCoverageNode[] {
  return graph.nodes.filter((node) => node.kind === "goal_claim");
}
