import type { ChannelDetail } from "../src/api/types.js";
import {
  mergeChannelConversationPages,
  mergeChannelConversationRefresh,
} from "../src/app/channelConversationState.js";

function assert(condition: unknown, message: string): asserts condition {
  if (!condition) throw new Error(message);
}

function detail(
  ids: string[],
  overrides: Partial<ChannelDetail> = {},
): ChannelDetail {
  return {
    channel_id: "ch-test",
    members: [],
    workflow_requests: [],
    messages: ids.map((message_id) => ({ message_id, text: message_id })),
    page: { limit: 2, returned: ids.length },
    ...overrides,
  };
}

const latest = detail(["m3", "m4"], { has_more: true, next_before: "m3" });
const earlier = detail(["m1", "m2"], { has_more: false, next_before: "" });
const expanded = mergeChannelConversationPages(latest, earlier);
assert(
  expanded.messages?.map((row) => row.message_id).join(",") === "m1,m2,m3,m4",
  "prepend should preserve chronological order",
);
assert(expanded.page?.returned === 4, "loaded page metadata should describe the merged window");

const refreshed = mergeChannelConversationRefresh(
  expanded,
  detail(["m4", "m5"], { has_more: true, next_before: "m4" }),
);
assert(
  refreshed.messages?.map((row) => row.message_id).join(",") === "m1,m2,m3,m4,m5",
  "a live refresh should retain already-loaded history and append new messages",
);
assert(refreshed.has_more === false, "a refresh should retain the oldest loaded cursor state");

const cleared = mergeChannelConversationRefresh(
  { ...expanded, history_clear_event_id: "clear-old" },
  detail(["m5"], { history_clear_event_id: "clear-new" }),
);
assert(
  cleared.messages?.map((row) => row.message_id).join(",") === "m5",
  "a history clear should discard the expanded pre-clear window",
);

console.log("channelConversationState tests passed");
