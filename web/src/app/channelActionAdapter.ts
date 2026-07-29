import type { ActionResponse, ChannelDetail, Snapshot } from "../api/types";
import {
  buildChannelWorkflowPlanningRequest,
  resolveChannelWorkflowBackend,
} from "../components/channel/workflowPlanning";
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
    await args.submitAction(
      decision === "confirm"
        ? "channel-consensus-confirm"
        : "channel-consensus-block",
      {
        channel_id: channelId(),
        thread_id: threadId,
        artifact_ref: artifactRef,
        artifact_digest: artifactDigest,
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
    args.prepareTaskAgent(taskId);
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

  return {
    decideConsensus,
    resolveQuestion,
    submitWorkflowRequest,
  };
}
