import type { ActionResponse, ChannelDetail, Snapshot } from "../api/types";
import {
  buildChannelWorkflowPlanningRequest,
  resolveChannelWorkflowBackend,
} from "../components/channel/workflowPlanning";
import {
  clearHistoryPayload,
  discussionModePayload,
  ownerReportPayload,
  startDiscussionPayload,
  synthesisRequestPayload,
} from "../components/channel/channelControlActions";
import {
  defaultKanbanThreadKey,
  kanbanAgentConversationId,
  kanbanAgentProjectId,
  kanbanThreadStorageKey,
} from "../components/orchestrator/kanbanAgentHistoryPolicy";

type SubmitAction = (
  action: string,
  payload: Record<string, unknown>,
) => Promise<ActionResponse>;

interface ChannelActionAdapterArgs {
  activeProjectId: string;
  channelDetail: ChannelDetail | null;
  prepareTaskAgent: (taskId: string) => void;
  readStoredBackend: () => string;
  selectedChannelId: string;
  snapshot: Snapshot | null;
  submitAction: SubmitAction;
}

export function createChannelActionAdapter(args: ChannelActionAdapterArgs) {
  const channelId = () => args.selectedChannelId || "ch-zaofu";

  async function setDiscussionMode(
    threadId: string,
    mode: string,
    defaultResponderId?: string,
  ) {
    await args.submitAction(
      "channel-discussion-mode",
      discussionModePayload(
        channelId(),
        threadId,
        mode,
        defaultResponderId
          ?? String(args.channelDetail?.discussion?.default_responder_id ?? ""),
      ),
    );
  }

  async function requestSynthesis(
    threadId: string,
    targetMemberId?: string,
  ) {
    await args.submitAction(
      "channel.synthesis.request",
      synthesisRequestPayload(channelId(), threadId, targetMemberId),
    );
  }

  async function generateOwnerReport(threadId: string) {
    await args.submitAction(
      "channel.owner_report.request",
      ownerReportPayload(channelId(), threadId),
    );
  }

  async function clearHistory(threadId: string) {
    await args.submitAction(
      "channel-clear-history",
      clearHistoryPayload(channelId(), threadId),
    );
  }

  async function startDiscussion(
    threadId: string,
    message: string,
    messageId: string,
    mode: string,
    restart = false,
  ) {
    await args.submitAction(
      "channel-discussion-start",
      startDiscussionPayload(
        channelId(), threadId, message, messageId, mode, restart,
      ),
    );
  }

  async function resolveQuestion(
    questionId: string,
    threadId: string,
    resolution: string,
    answer: string,
  ) {
    await args.submitAction("channel-question-resolve", {
      channel_id: channelId(),
      thread_id: threadId,
      question_id: questionId,
      resolution,
      answer,
      resolved_by: "owner:operator",
      source: "web-channel-question",
    });
  }

  async function decideConsensus(
    decision: "confirm" | "block",
    threadId: string,
    artifactRef: string,
    artifactDigest: string,
    blocker = "",
  ) {
    const consensus = args.channelDetail?.consensus?.[threadId] ?? {};
    const prdRevision = Number(consensus.prd_revision ?? 0);
    const readinessVerdict = String(
      consensus.readiness_verdict ?? "unassessed",
    );
    await args.submitAction(
      decision === "confirm"
        ? "channel-consensus-confirm"
        : "channel-consensus-block",
      {
        channel_id: channelId(),
        thread_id: threadId,
        artifact_ref: artifactRef,
        artifact_digest: artifactDigest,
        prd_revision: prdRevision,
        accept_readiness_risk: (
          readinessVerdict === "needs_owner"
          || readinessVerdict === "needs_multi_lens"
        ) || undefined,
        member_id: "owner:operator",
        blocker_question: blocker || undefined,
        source: "web-channel-consensus",
      },
    );
  }

  async function submitWorkflowRequest(
    taskId: string,
    objective: string,
    threadId: string,
  ) {
    const planning = buildChannelWorkflowPlanningRequest({
      channelId: channelId(),
      detail: args.channelDetail,
      objective,
      taskId,
      threadId,
    });
    if (!planning) return;
    const snapshotProjectId = args.snapshot?.project?.project_id || "";
    const projectId = kanbanAgentProjectId(
      args.activeProjectId,
      snapshotProjectId,
    );
    const defaultThread = defaultKanbanThreadKey(
      args.activeProjectId,
      snapshotProjectId,
    );
    const threadKey = typeof window === "undefined"
      ? defaultThread
      : window.localStorage.getItem(kanbanThreadStorageKey(projectId))
        || defaultThread;
    const agentSurface = args.snapshot?.runtime.agent_surface;
    const availableBackends = (agentSurface?.backends ?? [])
      .filter((item) => item.available !== false)
      .map((item) => item.id);
    const backend = resolveChannelWorkflowBackend({
      availableBackends,
      configuredBackends: [
        agentSurface?.configured_backend,
        agentSurface?.default_backend,
        agentSurface?.backend,
        ...availableBackends,
      ],
      storedBackend: args.readStoredBackend(),
    });
    const permissionProfile = agentSurface?.permission_profile || "dangerous_full";
    if (taskId) args.prepareTaskAgent(taskId);
    await args.submitAction("chat-orchestrator", {
      backend,
      permission_profile: permissionProfile,
      dangerous_ack: permissionProfile === "dangerous_full" || undefined,
      scope: "project",
      project_id: projectId,
      conversation_id: kanbanAgentConversationId(projectId),
      thread_key: threadKey,
      task_id: taskId,
      message: planning.message,
      workflow_context: planning.workflowContext,
      source: "web-channel-workflow-plan",
    });
  }

  async function adoptResearchResult(payload: Record<string, unknown>) {
    await args.submitAction("research-adopt", {
      ...payload,
      source: "web-channel-research-result",
    });
  }

  return {
    adoptResearchResult,
    clearHistory,
    decideConsensus,
    generateOwnerReport,
    requestSynthesis,
    resolveQuestion,
    setDiscussionMode,
    startDiscussion,
    submitWorkflowRequest,
  };
}
