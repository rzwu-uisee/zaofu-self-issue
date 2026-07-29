import {
  buildChannelWorkflowPlanningRequest,
  canonicalChannelPrd,
  resolveChannelWorkflowBackend,
} from "../src/components/channel/workflowPlanning.js";
import type { ChannelDetail } from "../src/api/types.js";

function assert(condition: unknown, message: string): asserts condition {
  if (!condition) throw new Error(message);
}

function assertEqual(actual: unknown, expected: unknown, message: string): void {
  assert(
    JSON.stringify(actual) === JSON.stringify(expected),
    `${message}: ${JSON.stringify(actual)}`,
  );
}

const detail = {
  channel_id: "ch-prd",
  name: "prd",
  members: [],
  workflow_requests: [],
  syntheses: [{
    event_id: "evt-synthesis",
    thread_id: "main",
    artifact_ref: "channel-artifacts/ch-prd/prd.md",
    artifact_digest: "sha256:canonical",
    source_refs: ["event:evt-requirement", "channel:ch-prd/main"],
  }],
  consensus: {
    main: {
      artifact_ref: "channel-artifacts/ch-prd/prd.md",
      artifact_digest: "canonical",
      reached_event_id: "evt-consensus",
    },
  },
} as ChannelDetail;

const canonical = canonicalChannelPrd(detail);
assert(canonical.ready, "canonical PRD should be ready");
assertEqual(canonical.synthesisEventId, "evt-synthesis", "synthesis id");

const request = buildChannelWorkflowPlanningRequest({
  channelId: "ch-prd",
  detail,
  objective: "Implement the accepted PRD.",
  taskId: "TASK-42",
});
assert(request, "planning request should be built");
assert(request.message.includes("TASK-42"), "message should bind the Task");
assert(request.message.includes("不要直接启动 workflow"), "message should preserve Plan boundary");
assertEqual(request.workflowContext.source_refs, {
  channel_id: "ch-prd",
  thread_id: "main",
  synthesis_event_id: "evt-synthesis",
  channel_consensus_event_id: "evt-consensus",
  channel_prd_ref: "channel-artifacts/ch-prd/prd.md",
  channel_prd_digest: "canonical",
}, "source refs");
assertEqual(request.workflowContext.artifact_refs, [{
  kind: "channel_prd",
  ref: "channel-artifacts/ch-prd/prd.md",
  digest: "canonical",
  source_event_id: "evt-synthesis",
  source_refs: ["event:evt-requirement", "channel:ch-prd/main"],
}], "artifact refs");

const unresolved = {
  ...detail,
  consensus: {
    main: {
      artifact_ref: "channel-artifacts/ch-prd/prd.md",
      artifact_digest: "canonical",
    },
  },
} as ChannelDetail;
assert(!canonicalChannelPrd(unresolved).ready, "unresolved consensus should not be canonical");
assertEqual(buildChannelWorkflowPlanningRequest({
  channelId: "ch-prd",
  detail: unresolved,
  objective: "",
  taskId: "TASK-42",
}), null, "unresolved consensus should block planning");

assertEqual(resolveChannelWorkflowBackend({
  storedBackend: "claude-headless",
}), "claude-headless", "light snapshot should retain the selected backend");
assertEqual(resolveChannelWorkflowBackend({
  availableBackends: ["codex-headless"],
  configuredBackends: ["claude-code", "codex"],
  storedBackend: "claude-headless",
}), "codex-headless", "unavailable stored backend should use an available config route");
assertEqual(resolveChannelWorkflowBackend({
  configuredBackends: ["claude-code"],
}), "claude-headless", "configured provider should map to its headless transport");
assertEqual(resolveChannelWorkflowBackend({}), "codex-headless", "empty context keeps the legacy fallback");

console.log("channelWorkflowPlanning tests passed");
