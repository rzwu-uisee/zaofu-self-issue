import { buildTerminalUsageRows } from "../src/components/agent-view/terminalUsageRows.js";
import type { TerminalSession, TerminalUsage } from "../src/components/terminal/types.js";

function assertEqual(actual: unknown, expected: unknown, message: string): void {
  if (actual !== expected) {
    throw new Error(`${message}: expected ${String(expected)}, got ${String(actual)}`);
  }
}

function usage(overrides: Partial<TerminalUsage> = {}): TerminalUsage {
  return {
    schema_version: "terminal-usage.v1",
    status: "observed",
    source: "provider_transcript",
    provider: "openai",
    accounting_mode: "subscription",
    model: "gpt-5.6-sol",
    models: ["gpt-5.6-sol"],
    fresh_input_tokens: 100,
    cached_input_tokens: 50,
    cache_creation_input_tokens: 0,
    input_tokens: 150,
    output_tokens: 20,
    reasoning_output_tokens: 5,
    total_tokens: 170,
    cost_usd: 0.01,
    cost_kind: "estimated",
    context_usage_ratio: 0.25,
    observed_at: "2026-08-20T15:00:00Z",
    reason: "",
    ...overrides,
  };
}

function session(id: string, overrides: Partial<TerminalSession> = {}): TerminalSession {
  return {
    session_id: id,
    slot: id,
    title: `Tab ${id}`,
    provider: "codex",
    provider_kind: "codex",
    project_id: "project-a",
    state: "active",
    generation: 1,
    created_at: "2026-08-20T15:00:00Z",
    updated_at: "2026-08-20T15:00:00Z",
    diagnostics: [],
    usage: usage(),
    ...overrides,
  };
}

const renamed = buildTerminalUsageRows([
  session("stable-id", { title: "API Review", generation: 3 }),
])[0];
assertEqual(renamed.key, "stable-id:g3", "accounting identity does not depend on tab title");
assertEqual(renamed.tab, "API Review", "latest tab title remains the display identity");
assertEqual(renamed.totalTokens, 170, "observed tokens remain per tab");
assertEqual(renamed.precision, "estimate · subscription", "subscription estimates are explicit");

const unavailable = buildTerminalUsageRows([
  session("waiting", {
    usage: usage({
      status: "awaiting_usage",
      total_tokens: null,
      cost_usd: null,
      cost_kind: "unavailable",
    }),
  }),
])[0];
assertEqual(unavailable.totalTokens, null, "missing usage is never presented as zero");
assertEqual(unavailable.costUsd, null, "missing cost is never presented as zero");
assertEqual(unavailable.precision, "awaiting usage", "awaiting state remains visible");

const historical = buildTerminalUsageRows([
  session("empty-stopped", { state: "stopped", usage: undefined }),
  session("used-stopped", { state: "stopped" }),
]);
assertEqual(historical.length, 1, "only stopped tabs with observed usage remain in the table");
assertEqual(historical[0].key, "used-stopped:g1", "settled stopped usage remains attributable");
