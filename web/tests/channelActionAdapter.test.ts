import {
  clearHistoryPayload,
  discussionModePayload,
  ownerReportPayload,
  startDiscussionPayload,
  synthesisRequestPayload,
} from "../src/components/channel/channelControlActions.js";
import { openOwnerQuestionnaire } from "../src/components/channel/channelQuestionnaire.js";
import type { ChannelDetail } from "../src/api/types.js";

function assert(condition: unknown, message: string): asserts condition {
  if (!condition) throw new Error(message);
}

const payloads = [
  discussionModePayload("ch-thread", "feature/api", "multi_lens", "critic"),
  synthesisRequestPayload("ch-thread", "feature/api", "critic"),
  ownerReportPayload("ch-thread", "feature/api"),
  clearHistoryPayload("ch-thread", "feature/api"),
  startDiscussionPayload(
    "ch-thread",
    "feature/api",
    "Design the API",
    "msg-api",
    "multi_lens",
  ),
];
assert(
  payloads.every((payload) => payload.thread_id === "feature/api"),
  "every channel control action must preserve the active thread",
);
assert(
  !payloads.some((payload) => payload.thread_id === "main"),
  "channel actions must not silently fall back to main",
);

const detail = {
  channel_id: "ch-thread",
  members: [],
  workflow_requests: [],
  open_questions: {
    open: {
      question_id: "q-open",
      thread_id: "feature/api",
      status: "open",
    },
    resolved: {
      question_id: "q-resolved",
      thread_id: "feature/api",
      status: "resolved",
    },
    other: {
      question_id: "q-other",
      thread_id: "main",
      status: "open",
    },
  },
  owner_questionnaires: {
    "feature/api": [
      { question_id: "q-open", question: "Open question" },
      { question_id: "q-resolved", question: "Resolved question" },
      { question_id: "q-missing", question: "Stale projection" },
    ],
  },
} as ChannelDetail;
const questionnaire = openOwnerQuestionnaire(detail, "feature/api");
assert(questionnaire.length === 1, "only one owner question remains open");
assert(questionnaire[0].question_id === "q-open", "the open question should remain");

console.log("channelActionAdapter tests passed");
