import { expect, test, type APIRequestContext, type Page } from "@playwright/test";

const token = process.env.ZF_WEB_ACTION_TOKEN_FOR_TEST ?? "";
const taskId = process.env.ZF_DOC156_TASK_ID ?? "";
const channelId = process.env.ZF_DOC156_CHANNEL_ID ?? "";
const requestId = process.env.ZF_DOC156_REQUEST_ID ?? "";

type EventItem = {
  seq: number;
  id: string;
  type: string;
  actor?: string;
  task_id?: string | null;
  causation_id?: string | null;
  correlation_id?: string | null;
  payload?: Record<string, unknown>;
};

type EventPage = {
  items?: EventItem[];
  current_seq?: number;
};

type EventDetail = {
  event?: EventItem;
};

type Snapshot = {
  project?: { project_id?: string };
  tasks?: Array<Record<string, unknown>>;
};

test.describe.configure({ mode: "serial", timeout: 240_000 });

async function apiJson<T>(request: APIRequestContext, path: string): Promise<T> {
  const response = await request.get(path);
  const body = await response.json().catch(() => ({}));
  expect(response.ok(), `${path}: ${response.status()} ${JSON.stringify(body)}`).toBeTruthy();
  return body as T;
}

async function projectId(request: APIRequestContext): Promise<string> {
  const snapshot = await apiJson<Snapshot>(request, "/api/snapshot");
  const id = String(snapshot.project?.project_id ?? "");
  expect(id, "isolated Doc 156 project id").not.toBe("");
  return id;
}

async function eventCursor(request: APIRequestContext, id: string): Promise<number> {
  const page = await apiJson<EventPage>(
    request,
    `/api/projects/${encodeURIComponent(id)}/events?limit=1`,
  );
  return Number(page.current_seq ?? 0);
}

async function eventsAfter(
  request: APIRequestContext,
  id: string,
  cursor: number,
): Promise<EventItem[]> {
  const page = await apiJson<EventPage>(
    request,
    `/api/projects/${encodeURIComponent(id)}/events?cursor=${cursor}&limit=500`,
  );
  return Array.isArray(page.items) ? page.items : [];
}

async function waitForEvents(
  request: APIRequestContext,
  id: string,
  cursor: number,
  predicate: (events: EventItem[]) => boolean,
  timeout = 60_000,
): Promise<EventItem[]> {
  let latest: EventItem[] = [];
  await expect.poll(async () => {
    latest = await eventsAfter(request, id, cursor);
    return predicate(latest);
  }, { timeout, intervals: [100, 250, 500, 1000] }).toBeTruthy();
  return latest;
}

async function hydrateEvent(
  request: APIRequestContext,
  id: string,
  event: EventItem,
): Promise<EventItem> {
  const detail = await apiJson<EventDetail>(
    request,
    `/api/projects/${encodeURIComponent(id)}/events/${encodeURIComponent(event.id)}`,
  );
  expect(detail.event, `hydrated event ${event.id}`).toBeDefined();
  return detail.event as EventItem;
}

async function openAgent(page: Page, id: string): Promise<void> {
  await page.addInitScript(({ actionToken }) => {
    window.localStorage.clear();
    window.localStorage.setItem("zf.operatorBackend", "claude-headless");
    window.localStorage.setItem("zf.webActionToken", actionToken);
  }, { actionToken: token });
  await page.goto(`/?project=${encodeURIComponent(id)}`);
  await expect(page.locator(".status-pill.status-live")).toBeVisible({ timeout: 90_000 });
  await page.getByRole("button", { name: "Open Kanban Agent" }).click();
  await expect(page.getByRole("dialog", { name: "Kanban Agent" })).toBeVisible();
}

async function reopenAgent(page: Page): Promise<void> {
  await page.reload();
  await expect(page.locator(".status-pill.status-live")).toBeVisible({ timeout: 90_000 });
  const dialog = page.getByRole("dialog", { name: "Kanban Agent" });
  if (!(await dialog.isVisible().catch(() => false))) {
    await page.getByRole("button", { name: "Open Kanban Agent" }).click();
  }
  await expect(dialog).toBeVisible();
}

async function sendProposal(page: Page, message: string, previewText: string): Promise<void> {
  const input = page.getByPlaceholder("Tell me what to do...");
  await input.fill(message);
  await page.getByRole("button", { name: "Send message" }).click();
  const card = page.locator(".agent-stack-card.proposal").filter({ hasText: previewText }).last();
  await expect(card).toBeVisible({ timeout: 30_000 });
  await expect(card.getByRole("button", { name: "Run action" })).toBeEnabled();
}

async function approveProposal(page: Page, previewText: string): Promise<void> {
  const card = page.locator(".agent-stack-card.proposal").filter({ hasText: previewText }).last();
  await expect(card).toBeVisible({ timeout: 30_000 });
  await card.getByRole("button", { name: "Run action" }).click();
}

function payloadText(event: EventItem): string {
  return JSON.stringify(event.payload ?? {});
}

test("Doc 156 Kanban Agent closes Channel, Research, adoption, and live Workflow start", async ({
  page,
  request,
}) => {
  expect(token).not.toBe("");
  expect(taskId).not.toBe("");
  expect(channelId).not.toBe("");
  expect(requestId).not.toBe("");

  const id = await projectId(request);
  const cursor = await eventCursor(request, id);
  const consoleErrors: string[] = [];
  page.on("console", (message) => {
    if (message.type() === "error") consoleErrors.push(message.text());
  });
  page.on("pageerror", (error) => consoleErrors.push(error.message));

  await openAgent(page, id);

  await sendProposal(
    page,
    "DOC156_CHANNEL_CREATE create the quick-change channel and wait for approval.",
    "quick-change",
  );
  await approveProposal(page, "quick-change");
  await waitForEvents(request, id, cursor, (events) => events.some((event) => (
    event.type === "channel.created"
    && event.payload?.channel_id === channelId
    && (event.payload?.scope as Record<string, unknown> | undefined)?.template !== undefined
  )));

  const channel = await apiJson<Record<string, unknown>>(
    request,
    `/api/channels/${encodeURIComponent(channelId)}`,
  );
  const channelText = JSON.stringify(channel);
  expect(channelText).toContain("quick-change");
  expect(channelText).toContain("tech_leader");
  expect(channelText).toContain("dev_reviewer");
  expect(channelText).toContain("qa_analyst");
  expect(channelText).toContain("workspace_writer");

  await sendProposal(
    page,
    "DOC156_DISCUSSION_START start the template discussion and wait for approval.",
    "Produce a structured delivery recommendation",
  );
  await approveProposal(page, "Produce a structured delivery recommendation");
  const discussionEvents = await waitForEvents(request, id, cursor, (events) => {
    const replies = events.filter((event) => (
      event.type === "channel.agent.reply.completed"
      && event.task_id === taskId
      && event.payload?.channel_id === channelId
      && event.payload?.thread_id === "main"
    ));
    return replies.length === 3 && events.some((event) => (
      event.type === "channel.synthesis.proposed"
      && event.payload?.channel_id === channelId
    ));
  }, 90_000);
  const synthesisSummary = discussionEvents.find((event) => (
    event.type === "channel.synthesis.proposed"
    && event.payload?.channel_id === channelId
  ));
  expect(synthesisSummary).toBeDefined();
  const synthesis = await hydrateEvent(request, id, synthesisSummary as EventItem);
  expect(synthesis.payload?.artifact_ref).toBeTruthy();
  expect(synthesis.payload?.artifact_digest).toBeTruthy();

  await sendProposal(
    page,
    "DOC156_RESEARCH_START run the fixed research fanout and wait for approval.",
    "Collect evidence for the Doc 156 delivery decision",
  );
  const pendingBeforeReload = await apiJson<{ items?: unknown[] }>(
    request,
    `/api/projects/${encodeURIComponent(id)}/kanban-agent/pending-proposals`,
  );
  expect(JSON.stringify(pendingBeforeReload.items ?? [])).toContain("research-start");

  await reopenAgent(page);
  await expect(page.locator(".agent-session")).toContainText("DOC156_RESEARCH_START");
  await expect(
    page.locator(".agent-stack-card.proposal").filter({
      hasText: "Collect evidence for the Doc 156 delivery decision",
    }).last(),
  ).toBeVisible();
  await approveProposal(page, "Collect evidence for the Doc 156 delivery decision");

  const researchEvents = await waitForEvents(request, id, cursor, (events) => events.some((event) => (
    event.type === "fanout.aggregate.completed"
    && event.payload?.stage_id === "research-fanout"
  )), 120_000);
  const aggregateSummary = researchEvents.find((event) => (
    event.type === "fanout.aggregate.completed"
    && event.payload?.stage_id === "research-fanout"
  ));
  expect(aggregateSummary).toBeDefined();
  const aggregate = await hydrateEvent(request, id, aggregateSummary as EventItem);
  expect(aggregate.payload?.research_artifact_ref).toBeTruthy();
  expect(aggregate.payload?.research_artifact_digest).toBeTruthy();
  const researchDispatches = researchEvents.filter((event) => (
    event.type === "fanout.child.dispatched"
    && event.payload?.stage_id === "research-fanout"
  ));
  expect(researchDispatches).toHaveLength(4);

  const adoption = {
    task_id: taskId,
    request_id: requestId,
    request_revision: 1,
    artifact_ref: String(aggregate.payload?.research_artifact_ref ?? ""),
    artifact_digest: String(aggregate.payload?.research_artifact_digest ?? ""),
    summary: String(aggregate.payload?.research_summary ?? "Doc 156 browser research."),
    channel_id: channelId,
    thread_id: "main",
  };
  await sendProposal(
    page,
    `DOC156_ADOPT ${JSON.stringify(adoption)}`,
    adoption.artifact_digest,
  );
  await approveProposal(page, adoption.artifact_digest);
  const adoptionEvents = await waitForEvents(
    request,
    id,
    cursor,
    (events) => events.some((event) => event.type === "workflow.research.adopted"),
  );
  const adoptedSummary = adoptionEvents.find(
    (event) => event.type === "workflow.research.adopted",
  );
  expect(adoptedSummary).toBeDefined();
  const adopted = await hydrateEvent(request, id, adoptedSummary as EventItem);
  expect(adopted.payload?.request_id).toBe(requestId);
  expect(adopted.payload?.artifact_digest).toBe(adoption.artifact_digest);

  const deliveryCursor = await eventCursor(request, id);
  await sendProposal(
    page,
    "DOC156_WORKFLOW_START start delivery-smoke and wait for approval.",
    "delivery-smoke",
  );
  await approveProposal(page, "delivery-smoke");
  const deliveryEvents = await waitForEvents(request, id, deliveryCursor, (events) => (
    events.some((event) => event.type === "workflow.invoke.accepted")
    && events.some((event) => event.type === "fanout.started")
    && events.some((event) => event.type === "fanout.child.dispatched")
    && events.some((event) => event.type === "worker.state.changed")
  ), 90_000);
  const deliveryDetails = await Promise.all(deliveryEvents
    .filter((event) => [
      "workflow.invoke.accepted",
      "fanout.started",
      "fanout.child.dispatched",
      "worker.state.changed",
    ].includes(event.type))
    .map((event) => hydrateEvent(request, id, event)));
  const invoke = deliveryDetails.find((event) => (
    event.type === "workflow.invoke.accepted"
    && event.payload?.pattern_id === "delivery-smoke"
  ));
  const fanout = deliveryDetails.find((event) => (
    event.type === "fanout.started"
    && event.payload?.stage_id === "delivery-smoke"
  ));
  const dispatch = deliveryDetails.find((event) => (
    event.type === "fanout.child.dispatched"
    && event.payload?.stage_id === "delivery-smoke"
  ));
  const busy = deliveryDetails.find((event) => (
    event.type === "worker.state.changed"
    && event.payload?.instance_id === "delivery_worker"
    && event.payload?.to === "busy"
  ));
  expect(invoke).toBeDefined();
  expect(fanout).toBeDefined();
  expect(dispatch).toBeDefined();
  expect(busy).toBeDefined();
  expect(invoke?.payload?.pattern_id).toBe("delivery-smoke");
  expect(fanout?.payload?.pdd_id).toBe(taskId);
  expect(dispatch?.payload?.role_instance).toBe("delivery_worker");
  expect(dispatch?.payload?.run_id).toBeTruthy();
  expect(busy?.payload?.to).toBe("busy");

  await waitForEvents(request, id, deliveryCursor, (events) => events.some((event) => (
    event.type === "codex.hook.user_prompt_submit"
    && event.actor === "delivery_worker"
    && Boolean(event.payload?.session_id)
  )), 120_000);

  const launchEvents = await apiJson<EventPage>(
    request,
    `/api/projects/${encodeURIComponent(id)}/events?limit=500&type=worker.launch_artifact.written`,
  );
  const launchSummary = (launchEvents.items ?? []).find((event) => (
    event.payload?.instance_id === "delivery_worker"
    && event.payload?.backend === "codex"
  ));
  expect(launchSummary).toBeDefined();
  const launch = await hydrateEvent(request, id, launchSummary as EventItem);
  expect(launch.payload?.artifact_ref).toBeTruthy();

  const allEvents = await eventsAfter(request, id, cursor);
  const markers = [
    "DOC156_CHANNEL_CREATE",
    "DOC156_DISCUSSION_START",
    "DOC156_RESEARCH_START",
    "DOC156_ADOPT",
    "DOC156_WORKFLOW_START",
  ];
  const completedTurns = markers.map((marker) => {
    const userMessage = allEvents.find((event) => (
      event.type === "user.message" && payloadText(event).includes(marker)
    ));
    expect(userMessage, `user.message for ${marker}`).toBeDefined();
    return allEvents.find((event) => (
      event.type === "kanban.agent.turn.completed"
      && event.correlation_id === userMessage?.correlation_id
    ));
  }).filter((event): event is EventItem => Boolean(event));
  expect(completedTurns).toHaveLength(5);
  const threadIds = new Set(completedTurns.map((event) => String(event.payload?.thread_id ?? "")));
  const providerSessions = new Set(
    completedTurns.map((event) => String(event.payload?.provider_session_id ?? "")),
  );
  expect(threadIds.size).toBe(1);
  expect(providerSessions.size).toBe(1);
  expect([...providerSessions][0]).not.toBe("");

  const pendingAfter = await apiJson<{ items?: unknown[] }>(
    request,
    `/api/projects/${encodeURIComponent(id)}/kanban-agent/pending-proposals`,
  );
  expect(pendingAfter.items ?? []).toHaveLength(0);

  await reopenAgent(page);
  for (const marker of markers) {
    await expect(page.locator(".agent-session")).toContainText(marker);
  }

  const productErrors = consoleErrors.filter((line) => !line.includes("favicon"));
  expect(productErrors.join("\n")).toBe("");
});
