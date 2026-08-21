import type { TerminalSession } from "../terminal/types";

export interface TerminalUsageRow {
  key: string;
  tab: string;
  provider: string;
  state: string;
  model: string;
  contextUsageRatio: number | null;
  totalTokens: number | null;
  costUsd: number | null;
  precision: string;
}

function precisionLabel(session: TerminalSession): string {
  const usage = session.usage;
  if (!usage) return "unavailable";
  if (usage.status !== "observed") {
    if (usage.status === "awaiting_usage") return "awaiting usage";
    if (usage.status === "unsupported") return "unsupported";
    return usage.reason || "unavailable";
  }
  const accounting = usage.accounting_mode === "subscription" ? " · subscription" : "";
  if (usage.cost_kind === "partial_estimate") return `partial estimate${accounting}`;
  if (usage.cost_kind === "estimated") return `estimate${accounting}`;
  if (usage.cost_kind === "unpriced") return `unpriced${accounting}`;
  return `${usage.cost_kind || "observed"}${accounting}`;
}

export function buildTerminalUsageRows(sessions: TerminalSession[]): TerminalUsageRow[] {
  return sessions
    .filter((session) => (
      session.state === "active"
      || session.state === "missing"
      || session.usage?.status === "observed"
    ))
    .sort((left, right) => {
      const activeOrder = Number(right.state === "active") - Number(left.state === "active");
      if (activeOrder) return activeOrder;
      const updatedOrder = right.updated_at.localeCompare(left.updated_at);
      return updatedOrder || left.title.localeCompare(right.title);
    })
    .map((session) => ({
      key: `${session.session_id}:g${session.generation}`,
      tab: session.title || session.slot,
      provider: session.provider,
      state: session.state,
      model: session.usage?.model || "-",
      contextUsageRatio: session.usage?.context_usage_ratio ?? null,
      totalTokens: session.usage?.status === "observed"
        ? (session.usage.total_tokens ?? null)
        : null,
      costUsd: session.usage?.status === "observed"
        ? (session.usage.cost_usd ?? null)
        : null,
      precision: precisionLabel(session),
    }));
}
