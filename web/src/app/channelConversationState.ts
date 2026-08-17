import type { ChannelDetail } from "../api/types";

function messageIdentity(message: Record<string, unknown>, fallback: number): string {
  return String(message.message_id || message.event_id || `row-${fallback}`);
}

function mergeMessages(
  first: Array<Record<string, unknown>>,
  second: Array<Record<string, unknown>>,
): Array<Record<string, unknown>> {
  const byId = new Map<string, Record<string, unknown>>();
  for (const message of [...first, ...second]) {
    byId.set(messageIdentity(message, byId.size), message);
  }
  return [...byId.values()];
}

export function mergeChannelConversationPages(
  current: ChannelDetail,
  earlier: ChannelDetail,
): ChannelDetail {
  const messages = mergeMessages(earlier.messages ?? [], current.messages ?? []);
  return {
    ...earlier,
    ...current,
    messages,
    has_more: earlier.has_more,
    next_before: earlier.next_before,
    page: {
      ...earlier.page,
      returned: messages.length,
    },
  };
}

export function mergeChannelConversationRefresh(
  current: ChannelDetail | null,
  fresh: ChannelDetail,
): ChannelDetail {
  if (!current || current.channel_id !== fresh.channel_id) return fresh;
  if (
    fresh.history_clear_event_id
    && fresh.history_clear_event_id !== current.history_clear_event_id
  ) {
    return fresh;
  }
  const currentMessages = current.messages ?? [];
  const pageLimit = Math.max(1, Number(current.page?.limit || 50));
  if (currentMessages.length <= pageLimit) return fresh;

  const messages = mergeMessages(currentMessages, fresh.messages ?? []);
  return {
    ...fresh,
    messages,
    has_more: current.has_more,
    next_before: current.next_before,
    page: {
      ...fresh.page,
      ...current.page,
      returned: messages.length,
      has_more: current.has_more,
      next_before: current.next_before,
    },
  };
}
