import {
  bootstrapEventCursor,
  channelEventRefreshPlan,
  pageLoadsChannels,
  pageLoadsDeliveryFeatures,
  pageLoadsRecentEvents,
  pageLoadsSnapshot,
  pagePollsOperatorInbox,
  pageResourcePlanForPage,
  projectionNeedsFresh,
  snapshotLoadKindForPage,
} from "../src/app/pageLoadPolicy.js";

function assert(condition: unknown, message: string): void {
  if (!condition) throw new Error(message);
}

function testChannelsUseSlimPath(): void {
  assert(snapshotLoadKindForPage("channels") === "none", "channels must not wait for project snapshot");
  assert(!pageLoadsSnapshot("channels"), "channels should load channel/read-event slices only");
  assert(pageLoadsChannels("channels"), "channels should own its summary resource");
  assert(!pageLoadsRecentEvents("channels"), "channel history should come from its scoped detail resource");
  assert(!pageLoadsDeliveryFeatures("channels"), "channels should not bootstrap delivery features");
  assert(!pagePollsOperatorInbox("channels"), "channels should not poll operator inbox");
}

function testWorkflowProposalsUseScopedProjection(): void {
  assert(snapshotLoadKindForPage("workflows") === "none", "workflow proposals should not wait for snapshot");
  assert(!pageLoadsSnapshot("workflows"), "workflow proposals should load the request slice only");
  assert(!pageLoadsChannels("workflows"), "workflow proposals should not load channel summary");
  assert(!pageLoadsRecentEvents("workflows"), "workflow proposals should not load the global event window");
  assert(!pageLoadsDeliveryFeatures("workflows"), "workflow proposals should not load delivery features");
  assert(!pagePollsOperatorInbox("workflows"), "workflow proposals should not poll operator inbox");
}

function testMeasureUsesDeliverySlice(): void {
  assert(snapshotLoadKindForPage("delivery") === "none", "delivery overview should not wait for snapshot");
  assert(snapshotLoadKindForPage("delivery-trace") === "none", "delivery trace should not wait for snapshot");
  assert(snapshotLoadKindForPage("goal-coverage") === "none", "goal coverage should not wait for snapshot");
  assert(snapshotLoadKindForPage("behavior-loop") === "none", "loop page should not wait for snapshot");
  assert(pageLoadsDeliveryFeatures("delivery"), "delivery overview should load delivery features");
  assert(pageLoadsDeliveryFeatures("goal-coverage"), "goal coverage should load delivery features");
  assert(pageLoadsDeliveryFeatures("behavior-loop"), "loop page should load delivery features");
}

function testSnapshotPagesStayExplicit(): void {
  assert(snapshotLoadKindForPage("board") === "light", "board should use light snapshot");
  assert(snapshotLoadKindForPage("task") === "light", "task detail shell should use light snapshot");
  assert(snapshotLoadKindForPage("traces") === "light", "trace compatibility route should keep the scoped light path");
  assert(snapshotLoadKindForPage("events") === "full", "events should use full observability snapshot");
  assert(snapshotLoadKindForPage("runs") === "full", "runs should use full observability snapshot");
}

function testInboxPollIsPageScoped(): void {
  assert(pagePollsOperatorInbox("inbox"), "inbox should poll operator inbox");
  assert(!pagePollsOperatorInbox("board"), "board should not poll operator inbox on initial load");
  assert(!pagePollsOperatorInbox("delivery"), "measure pages should not poll operator inbox on initial load");
}

function testBootstrapManifestIsPageOwned(): void {
  const channelPlan = pageResourcePlanForPage("channels").foreground;
  assert(channelPlan.includes("project.health"), "all project pages need the SSE cursor health resource");
  assert(channelPlan.includes("channels.summary"), "channel plan should include channel summary");
  assert(!channelPlan.includes("events.recent"), "channel plan should not duplicate scoped history");

  const boardPlan = pageResourcePlanForPage("board").foreground;
  assert(boardPlan.includes("project.snapshot.light"), "board should own light snapshot during migration");
  assert(!boardPlan.includes("channels.summary"), "board should not bootstrap channels");
  assert(!boardPlan.includes("events.recent"), "board should not bootstrap recent events");

  const triagePlan = pageResourcePlanForPage("triage").foreground;
  assert(triagePlan.includes("events.recent"), "triage needs the bounded failed-event window");

  const inboxPlan = pageResourcePlanForPage("inbox").foreground;
  assert(inboxPlan.includes("operator.inbox"), "inbox should own its projection");
  assert(!inboxPlan.includes("channels.summary"), "inbox should not bootstrap channels");
}

function testRecentWindowOwnsItsSseCursor(): void {
  assert(
    bootstrapEventCursor({ recentEventsSeq: 8, healthSeq: 10, snapshotSeq: 9 }) === 8,
    "a stale recent window must replay its tail through SSE instead of skipping to health",
  );
  assert(
    bootstrapEventCursor({ healthSeq: 10, snapshotSeq: 9, fallbackSeq: 7 }) === 10,
    "pages without event bodies should connect from the latest cheap cursor",
  );
}

function testProjectionFreshnessIncludesLayoutStale(): void {
  assert(projectionNeedsFresh({ projection_state: "ready", tail_behind: true }), "tail lag needs a fresh follow-up");
  assert(projectionNeedsFresh({ projection_state: "stale", tail_behind: false }), "rotation stale needs a fresh follow-up");
  assert(!projectionNeedsFresh({ projection_state: "ready", tail_behind: false }), "ready projection should not refetch");
}

function testChannelEventRefreshPlanSeparatesSummaryFromConversation(): void {
  assert(
    channelEventRefreshPlan("channel.agent.reply.completed").summary,
    "terminal replies change Channel attention summary",
  );
  assert(
    channelEventRefreshPlan("channel.message.posted").summary,
    "posted messages change Channel summary counts",
  );
  assert(
    !channelEventRefreshPlan("channel.message.stream.delta").summary,
    "stream deltas must not rebuild the Channel list",
  );
  assert(
    channelEventRefreshPlan("channel.message.stream.delta").conversation,
    "stream deltas still refresh the selected conversation",
  );
  assert(
    !channelEventRefreshPlan("task.updated").conversation,
    "non-Channel events do not refresh Channel slices",
  );
}

testChannelsUseSlimPath();
testWorkflowProposalsUseScopedProjection();
testMeasureUsesDeliverySlice();
testSnapshotPagesStayExplicit();
testInboxPollIsPageScoped();
testBootstrapManifestIsPageOwned();
testRecentWindowOwnsItsSseCursor();
testProjectionFreshnessIncludesLayoutStale();
testChannelEventRefreshPlanSeparatesSummaryFromConversation();
