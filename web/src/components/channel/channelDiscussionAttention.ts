import type {
  ChannelDetail,
  ChannelDiscussionAttention,
} from "../../api/types";

export type ChannelDiscussionAttentionAction =
  | "activity"
  | "questions"
  | "result"
  | "synthesize"
  | "restart";

export interface ChannelDiscussionAttentionPresentation {
  label: string;
  summary: string;
  tone: "info" | "warning" | "ready" | "blocked" | "done";
  action: ChannelDiscussionAttentionAction;
  actionLabel: string;
  visible: boolean;
}

export interface ChannelDiscussionControlPolicy {
  canStartOrRestart: boolean;
  canDrainReplies: boolean;
  canSynthesize: boolean;
  restart: boolean;
  startLabel: "Start discussion" | "Restart discussion";
}

const STATE_PRIORITY: Record<ChannelDiscussionAttention["state"], number> = {
  needs_input: 0,
  blocked: 1,
  running: 2,
  ready: 3,
  done: 4,
};

function countLabel(count: number, singular: string, plural = `${singular}s`): string {
  return `${count} ${count === 1 ? singular : plural}`;
}

export function selectChannelDiscussionAttention(
  detail: ChannelDetail | null | undefined,
  preferredThreadId: string,
): ChannelDiscussionAttention | null {
  const rows = Object.values(detail?.discussion_attention ?? {});
  if (!rows.length) return null;
  const preferred = detail?.discussion_attention?.[preferredThreadId];
  if (preferred && preferred.state !== "done") return preferred;
  const selected = [...rows].sort((left, right) => (
    STATE_PRIORITY[left.state] - STATE_PRIORITY[right.state]
  ))[0] ?? null;
  return selected?.state !== "done" ? selected : preferred ?? selected;
}

export function presentChannelDiscussionAttention(
  attention: ChannelDiscussionAttention,
): ChannelDiscussionAttentionPresentation {
  if (attention.state === "needs_input") {
    if (attention.owner_question_count > 0) {
      const activeSuffix = attention.active_agent_count > 0
        ? ` · ${countLabel(attention.active_agent_count, "agent")} still responding`
        : "";
      return {
        label: "Needs input",
        summary: `${countLabel(attention.owner_question_count, "owner decision")} waiting${activeSuffix}`,
        tone: "warning",
        action: "questions",
        actionLabel: "Review decisions",
        visible: true,
      };
    }
    const activeSuffix = attention.active_agent_count > 0
      ? ` · ${countLabel(attention.active_agent_count, "agent")} still responding`
      : "";
    return {
      label: "Needs input",
      summary: `The synthesized result needs an owner decision${activeSuffix}`,
      tone: "warning",
      action: "result",
      actionLabel: "Review result",
      visible: true,
    };
  }
  if (attention.state === "running") {
    return {
      label: "Running",
      summary: attention.active_agent_count > 0
        ? `${countLabel(attention.active_agent_count, "agent")} responding`
        : "Discussion work is in progress",
      tone: "info",
      action: "activity",
      actionLabel: "View activity",
      visible: true,
    };
  }
  if (attention.state === "ready") {
    return {
      label: "Ready",
      summary: `${countLabel(attention.completed_reply_count, "reply", "replies")} complete`,
      tone: "ready",
      action: "synthesize",
      actionLabel: "Synthesize",
      visible: true,
    };
  }
  if (attention.state === "blocked") {
    return {
      label: "Blocked",
      summary: attention.failed_reply_count > 0
        ? `${countLabel(attention.failed_reply_count, "reply failure")} needs attention`
        : "Discussion recovery is required",
      tone: "blocked",
      action: "restart",
      actionLabel: "Review blocker",
      visible: true,
    };
  }
  return {
    label: "Complete",
    summary: "Discussion complete",
    tone: "done",
    action: "result",
    actionLabel: "View result",
    visible: false,
  };
}

export function channelDiscussionControlPolicy(
  attention: ChannelDiscussionAttention | null,
  kernelState: string,
  hasRequirement: boolean,
): ChannelDiscussionControlPolicy {
  const restartsBlockedDiscussion = Boolean(attention?.can_restart);
  const startsNewDiscussion = (
    kernelState === "idle" && !restartsBlockedDiscussion
  );
  return {
    canStartOrRestart: Boolean(
      hasRequirement
      && (startsNewDiscussion || restartsBlockedDiscussion),
    ),
    canDrainReplies: Boolean(attention?.can_drain_replies),
    canSynthesize: Boolean(attention?.can_synthesize),
    restart: restartsBlockedDiscussion,
    startLabel: restartsBlockedDiscussion
      ? "Restart discussion"
      : "Start discussion",
  };
}
