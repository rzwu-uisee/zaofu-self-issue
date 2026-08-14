import type { CostSummary } from "../src/api/types.js";
import { buildProjectCostPresentation } from "../src/app/costPrecision.js";

function assertEqual(actual: unknown, expected: unknown, message: string): void {
  if (actual !== expected) {
    throw new Error(`${message}: expected ${String(expected)}, got ${String(actual)}`);
  }
}

function summary(overrides: Partial<CostSummary> = {}): CostSummary {
  return {
    total_usd: 1.25,
    per_role: {
      dev: {
        usd: 1.25,
        input_tokens: 100,
        output_tokens: 25,
        entries: 3,
      },
    },
    precision: {
      schema_version: "cost-precision-summary.v1",
      display_total_usd: 1.25,
      estimated_usd: 0,
      provider_reported_usd: 0,
      billed_usd: 0,
      unpriced_entries: 0,
      partial_entries: 0,
      statuses: {},
      catalogs: [],
    },
    reconciliation: null,
    ...overrides,
  };
}

const legacy = buildProjectCostPresentation(summary({
  precision: {
    ...summary().precision!,
    statuses: { legacy: 3 },
  },
}));
assertEqual(legacy.totalTokens, 125, "project token aggregation remains canonical");
assertEqual(legacy.totalUsd, 1.25, "project cost aggregation remains canonical");
assertEqual(legacy.precisionLabel, "legacy · unreconciled", "legacy status is explicit");
assertEqual(legacy.precisionTone, "warn", "legacy status warns");

const oldServer = buildProjectCostPresentation(summary({ precision: undefined }));
assertEqual(oldServer.precisionLabel, "legacy · unreconciled", "missing precision fails closed");

const risky = buildProjectCostPresentation(summary({
  precision: {
    ...summary().precision!,
    unpriced_entries: 2,
    partial_entries: 1,
    statuses: { legacy: 3 },
  },
}));
assertEqual(
  risky.precisionLabel,
  "2 unpriced · 1 partial · legacy · unreconciled",
  "risk states remain visible",
);

const reconciled = buildProjectCostPresentation(summary({
  precision: { ...summary().precision!, billed_usd: 1.2 },
  reconciliation: {
    provider: "openai",
    accounting_mode: "api",
    status: "reconciled",
  },
}));
assertEqual(reconciled.precisionLabel, "billed reconciled", "billed status wins");
assertEqual(reconciled.precisionTone, "ok", "reconciled billing is healthy");

const providerReported = buildProjectCostPresentation(summary({
  precision: {
    ...summary().precision!,
    estimated_usd: 1.1,
    provider_reported_usd: 1.2,
  },
}));
assertEqual(providerReported.precisionLabel, "provider reported", "provider cost is not relabeled estimate");

const subscriptionEstimate = buildProjectCostPresentation(summary({
  precision: { ...summary().precision!, estimated_usd: 1.1 },
  reconciliation: {
    provider: "openai",
    accounting_mode: "subscription",
    status: "not_available",
  },
}));
assertEqual(
  subscriptionEstimate.precisionLabel,
  "catalog estimate · billing unavailable",
  "subscription estimate does not pretend to be billed",
);

const empty = buildProjectCostPresentation(null);
assertEqual(empty.hasUsage, false, "empty projection has no usage");
assertEqual(empty.precisionLabel, "", "empty projection has no precision claim");
assertEqual(empty.precisionTone, "muted", "empty projection is muted");
