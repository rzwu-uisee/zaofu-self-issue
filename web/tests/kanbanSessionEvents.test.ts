import type { RecentEvent } from "../src/api/types.js";
import {
  kanbanAgentSessionEventsFromLive,
  mergeBoundedKanbanSessionEvents,
  mergeEventsByIdentity,
} from "../src/components/orchestrator/kanbanSessionEvents.js";
import {
  parsePlanRequest,
  parsePlanResponse,
} from "../src/components/agent-session/agentUiEvent.js";

function assert(condition: unknown, message: string): void {
  if (!condition) throw new Error(message);
}

function event(seq: number, type: string, payload: Record<string, unknown>, taskId = ""): RecentEvent {
  return {
    id: `evt-${seq}`,
    seq,
    ts: `2026-06-29T12:00:${String(seq).padStart(2, "0")}.000Z`,
    type,
    task_id: taskId || null,
    payload,
  };
}

const scope = {
  projectId: "proj-a",
  conversationId: "kanban:proj-a",
  backend: "codex-headless",
  taskId: "",
};

const liveTurn = [
  event(1, "user.message", {
    source: "kanban",
    target: "kanban-agent",
    runtime_delivery: "headless",
    backend: "codex-headless",
    project_id: "proj-a",
    conversation_id: "kanban:proj-a",
    thread_key: "thread-a",
    message: "review docs",
  }),
  event(2, "kanban.agent.turn.created", {
    backend: "codex-headless",
    project_id: "proj-a",
    conversation_id: "kanban:proj-a",
    thread_key: "thread-a",
    turn_id: "turn-a",
    message_event_id: "evt-1",
  }),
  event(3, "kanban.agent.turn.delta", {
    backend: "codex-headless",
    project_id: "proj-a",
    conversation_id: "kanban:proj-a",
    thread_key: "thread-a",
    turn_id: "turn-a",
    message_type: "text",
    content: "working",
  }),
  event(4, "kanban.agent.reply", {
    backend: "codex-headless",
    project_id: "proj-a",
    conversation_id: "kanban:proj-a",
    thread_key: "thread-a",
    turn_id: "turn-a",
    answer: "done",
  }),
];

const unrelatedChannelRun = event(5, "agent.session.part.delta", {
  backend: "codex-headless",
  project_id: "proj-a",
  conversation_id: "channel:ch-a",
  thread_id: "thread-a",
  source: "channel-agent.headless",
  content: "channel-only",
});

const initialScoped = kanbanAgentSessionEventsFromLive([...liveTurn, unrelatedChannelRun], scope);
assert(initialScoped.length === 4, `should keep only kanban conversation events, got ${initialScoped.length}`);

const buffered = mergeBoundedKanbanSessionEvents([], initialScoped);
const afterPageSwitchLiveEvents: RecentEvent[] = [];
const conversationEvents = mergeEventsByIdentity(buffered, afterPageSwitchLiveEvents);
const retainedPrompt = conversationEvents.find((item) => item.type === "user.message");
const retainedReply = conversationEvents.find((item) => item.type === "kanban.agent.reply");

assert(retainedPrompt?.payload?.message === "review docs", "buffer should preserve user prompt after live events reset");
assert(retainedReply?.payload?.answer === "done", "buffer should preserve reply after live events reset");

const bounded = mergeBoundedKanbanSessionEvents([], [
  event(10, "kanban.agent.reply", { backend: "codex-headless", answer: "a" }),
  event(11, "kanban.agent.reply", { backend: "codex-headless", answer: "b" }),
  event(12, "kanban.agent.reply", { backend: "codex-headless", answer: "c" }),
  event(13, "kanban.agent.reply", { backend: "codex-headless", answer: "d" }),
], 3);
assert(bounded.map((item) => item.seq).join(",") === "11,12,13", "bounded buffer should keep newest events");

// Regression (live-stream backend-agnostic fold, consistent with b7eebff4):
// a kanban thread is one durable conversation that can span backends. A live
// codex delta must still fold into the view when the operator's selector
// currently reads claude — backend is advisory, never an exclusion. Before the
// fix this delta was dropped and the reply stayed stuck on "thinking".
const claudeScope = {
  projectId: "proj-a",
  conversationId: "kanban:proj-a",
  backend: "claude-headless",
  taskId: "",
};
const codexDeltaUnderClaudeSelector = kanbanAgentSessionEventsFromLive([
  event(20, "kanban.agent.turn.delta", {
    backend: "codex",
    provider: "codex",
    project_id: "proj-a",
    conversation_id: "kanban:proj-a",
    turn_id: "turn-x",
    content: "streamed",
  }),
], claudeScope);
assert(
  codexDeltaUnderClaudeSelector.length === 1,
  `codex live delta must fold under a claude selector (backend advisory), got ${codexDeltaUnderClaudeSelector.length}`,
);

const livePlanEvents = kanbanAgentSessionEventsFromLive([
  event(30, "kanban.agent.plan.requested", {
    project_id: "proj-a",
    conversation_id: "kanban:proj-a",
    thread_key: "thread-a",
    plan_request: { request_id: "plan-route" },
  }),
  event(31, "kanban.agent.plan.answered", {
    project_id: "proj-a",
    conversation_id: "kanban:proj-a",
    thread_key: "thread-a",
    request_id: "plan-route",
  }),
], scope);
assert(livePlanEvents.length === 2, "Plan request and answer events stay in the live session buffer");

const multiQuestionPlan = parsePlanRequest({
  plan_request: {
    request_event_id: "evt-plan-multi",
    request_id: "plan-multi",
    revision: 2,
    header: "Delivery inputs",
    questions: [
      {
        id: "route",
        question: "Which route?",
        options: [
          { id: "direct", label: "Direct", recommended: true },
          { id: "research", label: "Research" },
        ],
      },
      {
        id: "evidence",
        question: "Which evidence depth?",
        options: [
          { id: "focused", label: "Focused", recommended: true },
          { id: "broad", label: "Broad" },
        ],
      },
    ],
  },
});
assert(multiQuestionPlan?.questions.length === 2, "Plan projection should retain both questions");
assert(
  multiQuestionPlan?.questions.every((question) => question.options[0]?.recommended),
  "Plan projection should retain each recommended option",
);
const multiQuestionResponse = parsePlanResponse({
  request_event_id: "evt-plan-multi",
  request_id: "plan-multi",
  revision: 2,
  answers: [
    { question_id: "route", option_id: "direct", answer: "Direct" },
    { question_id: "evidence", option_id: "focused", answer: "Focused" },
  ],
});
assert(
  multiQuestionResponse?.answers.length === 2,
  "Plan response projection should retain the atomic answer set",
);

const proposalReceipts = kanbanAgentSessionEventsFromLive([
  event(39, "operator.action.proposed", {
    project_id: "proj-a",
    conversation_id: "kanban:proj-a",
    thread_key: "thread-a",
    proposal: { proposal_event_id: "evt-proposal" },
  }),
  event(40, "operator.action.resolved", {
    project_id: "proj-a",
    conversation_id: "kanban:proj-a",
    thread_key: "thread-a",
    proposal_event_id: "evt-proposal",
    resolution: "dismissed",
  }),
  event(41, "task.created", {
    proposal_event_id: "evt-proposal-2",
    request: { proposal_event_id: "evt-proposal-2" },
  }),
  event(42, "operator.action.proposed", {
    project_id: "proj-a",
    source: "cli",
    proposal: { proposal_event_id: "evt-unscoped" },
  }),
], scope);
assert(
  proposalReceipts.length === 3,
  "scoped proposal requests and receipts stay in the live session buffer without leaking CLI proposals",
);

console.log("kanbanSessionEvents.test.ts OK");
