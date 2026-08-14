import type { ActionResponse } from "../api/types";

export function shouldRefreshChannelsAfterAction(
  action: string,
  result: Pick<ActionResponse, "ok" | "applied_action" | "channel_id">,
): boolean {
  if (action.startsWith("channel")) return true;
  if (action !== "kanban-plan-apply" || !result.ok) return false;
  return String(result.applied_action ?? "").startsWith("channel")
    || Boolean(String(result.channel_id ?? "").trim());
}

export function planDiscussionBackend(
  planBackend: unknown,
  selectedBackend: string,
): string {
  return String(planBackend ?? "").trim() || selectedBackend;
}
