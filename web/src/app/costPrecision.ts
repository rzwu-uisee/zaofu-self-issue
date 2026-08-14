import type { CostSummary } from "../api/types";

export type CostPrecisionTone = "info" | "ok" | "warn" | "muted";

export interface ProjectCostPresentation {
  entries: number;
  hasUsage: boolean;
  inputTokens: number;
  outputTokens: number;
  precisionLabel: string;
  precisionTone: CostPrecisionTone;
  totalTokens: number;
  totalUsd: number;
}

function count(value: unknown): number {
  const parsed = Number(value ?? 0);
  return Number.isFinite(parsed) && parsed > 0 ? parsed : 0;
}

export function buildProjectCostPresentation(
  cost: CostSummary | null | undefined,
): ProjectCostPresentation {
  const rows = Object.values(cost?.per_role ?? {});
  const inputTokens = rows.reduce((total, row) => total + count(row.input_tokens), 0);
  const outputTokens = rows.reduce((total, row) => total + count(row.output_tokens), 0);
  const entries = rows.reduce((total, row) => total + count(row.entries), 0);
  const roleUsd = rows.reduce((total, row) => total + count(row.usd), 0);
  const totalUsd = count(cost?.total_usd) || roleUsd;
  const totalTokens = inputTokens + outputTokens;
  const hasUsage = entries > 0 || totalTokens > 0 || totalUsd > 0;
  const precision = cost?.precision;
  const reconciliationStatus = String(cost?.reconciliation?.status ?? "").trim();
  const unpricedEntries = count(precision?.unpriced_entries);
  const partialEntries = count(precision?.partial_entries);
  const legacyEntries = count(precision?.statuses?.legacy)
    || (!precision && hasUsage ? entries || 1 : 0);

  const riskLabels: string[] = [];
  if (unpricedEntries) riskLabels.push(`${unpricedEntries} unpriced`);
  if (partialEntries) riskLabels.push(`${partialEntries} partial`);
  if (legacyEntries) riskLabels.push("legacy");
  if (riskLabels.length) {
    return {
      entries,
      hasUsage,
      inputTokens,
      outputTokens,
      precisionLabel: `${riskLabels.join(" · ")} · unreconciled`,
      precisionTone: "warn",
      totalTokens,
      totalUsd,
    };
  }

  let precisionLabel = "";
  let precisionTone: CostPrecisionTone = hasUsage ? "info" : "muted";
  if (reconciliationStatus === "reconciled") {
    precisionLabel = "billed reconciled";
    precisionTone = "ok";
  } else if (count(precision?.billed_usd)) {
    precisionLabel = "billed · unreconciled";
    precisionTone = "warn";
  } else if (count(precision?.provider_reported_usd)) {
    precisionLabel = "provider reported";
  } else if (count(precision?.estimated_usd)) {
    precisionLabel = reconciliationStatus === "not_available"
      ? "catalog estimate · billing unavailable"
      : "catalog estimate";
  } else if (reconciliationStatus === "not_available") {
    precisionLabel = "billing unavailable";
  } else if (reconciliationStatus) {
    precisionLabel = `billing ${reconciliationStatus}`;
    precisionTone = "warn";
  }

  return {
    entries,
    hasUsage,
    inputTokens,
    outputTokens,
    precisionLabel,
    precisionTone,
    totalTokens,
    totalUsd,
  };
}
