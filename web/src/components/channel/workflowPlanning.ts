import type { ChannelDetail } from "../../api/types";

export interface CanonicalChannelPrd {
  artifactDigest: string;
  artifactRef: string;
  consensusEventId: string;
  ready: boolean;
  sourceRefs: string[];
  synthesisEventId: string;
}

export interface ChannelWorkflowPlanningRequest {
  message: string;
  workflowContext: Record<string, unknown>;
}

export type ChannelWorkflowBackend = "claude-headless" | "codex-headless";

function text(value: unknown): string {
  return typeof value === "string" ? value.trim() : "";
}

function headlessBackend(value: unknown): ChannelWorkflowBackend | null {
  const backend = text(value).toLowerCase();
  if (backend.includes("claude")) return "claude-headless";
  if (backend.includes("codex")) return "codex-headless";
  return null;
}

export function resolveChannelWorkflowBackend(args: {
  availableBackends?: unknown[];
  configuredBackends?: unknown[];
  storedBackend?: unknown;
}): ChannelWorkflowBackend {
  const available = new Set(
    (args.availableBackends ?? [])
      .map((item) => headlessBackend(item))
      .filter((item): item is ChannelWorkflowBackend => item !== null),
  );
  const stored = headlessBackend(args.storedBackend);
  if (stored && (!available.size || available.has(stored))) return stored;
  for (const candidate of args.configuredBackends ?? []) {
    const configured = headlessBackend(candidate);
    if (configured && (!available.size || available.has(configured))) {
      return configured;
    }
  }
  return available.values().next().value ?? "codex-headless";
}

export function canonicalChannelPrd(
  detail: ChannelDetail | null,
  threadId = "main",
): CanonicalChannelPrd {
  const consensus = detail?.consensus?.[threadId] ?? {};
  const artifactRef = text(consensus.artifact_ref);
  const artifactDigest = text(consensus.artifact_digest);
  const consensusEventId = text(consensus.reached_event_id);
  const synthesis = [...(detail?.syntheses ?? [])].reverse().find((item) => (
    text(item.thread_id) === threadId
    && text(item.artifact_ref) === artifactRef
    && text(item.artifact_digest).replace(/^sha256:/, "")
      === artifactDigest.replace(/^sha256:/, "")
  ));
  return {
    artifactDigest,
    artifactRef,
    consensusEventId,
    ready: Boolean(
      artifactRef
      && artifactDigest
      && consensusEventId
      && synthesis,
    ),
    sourceRefs: Array.isArray(synthesis?.source_refs)
      ? synthesis.source_refs.map((item) => text(item)).filter(Boolean)
      : [],
    synthesisEventId: text(synthesis?.event_id),
  };
}

export function buildChannelWorkflowPlanningRequest(args: {
  channelId: string;
  detail: ChannelDetail | null;
  objective: string;
  taskId: string;
  threadId?: string;
}): ChannelWorkflowPlanningRequest | null {
  const threadId = text(args.threadId) || "main";
  const taskId = text(args.taskId);
  const channelId = text(args.channelId);
  const objective = text(args.objective);
  const prd = canonicalChannelPrd(args.detail, threadId);
  if (!taskId || !channelId || !prd.ready) return null;

  const sourceRefs = {
    channel_id: channelId,
    thread_id: threadId,
    synthesis_event_id: prd.synthesisEventId,
    channel_consensus_event_id: prd.consensusEventId,
    channel_prd_ref: prd.artifactRef,
    channel_prd_digest: prd.artifactDigest,
  };
  const artifactRefs = [{
    kind: "channel_prd",
    ref: prd.artifactRef,
    digest: prd.artifactDigest,
    source_event_id: prd.synthesisEventId,
    source_refs: prd.sourceRefs,
  }];
  const workflowContext = {
    channel_id: channelId,
    thread_id: threadId,
    synthesis_event_id: prd.synthesisEventId,
    source_ref: prd.artifactRef,
    source_refs: sourceRefs,
    artifact_refs: artifactRefs,
    expected_output: objective || "Complete the approved Task.",
  };
  const lineage = JSON.stringify({
    task_id: taskId,
    ...sourceRefs,
    source_refs: prd.sourceRefs,
  }, null, 2);
  const message = [
    `为现有 Task ${taskId} 规划执行 workflow。`,
    objective ? `目标：${objective}` : "",
    "请根据注入的 workflow route catalog 和 Task 复杂度给出 2-3 个 task_workflow Plan 选项，其中一个标记 recommended；可包含一个不启动 workflow 的 continue 选项。",
    "每个执行选项必须使用 effect.mode=propose、effect.action=workflow-start，并绑定当前 task_id、有效 route_id 和 objective。只返回一个 plan_request JSON，不要直接启动 workflow，也不要创建新 Task。",
    "Canonical Channel PRD lineage:",
    lineage,
  ].filter(Boolean).join("\n\n");
  return { message, workflowContext };
}
