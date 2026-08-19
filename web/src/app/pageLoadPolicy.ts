import type { PageId } from "./sharedTypes";

export type SnapshotLoadKind = "none" | "light" | "full";

export type BootstrapResource =
  | "project.health"
  | "project.snapshot.light"
  | "project.snapshot.full"
  | "delivery.features"
  | "channels.summary"
  | "events.recent"
  | "operator.inbox";

export interface PageResourcePlan {
  foreground: readonly BootstrapResource[];
}

export interface BootstrapCursorSources {
  recentEventsSeq?: number | null;
  snapshotSeq?: number | null;
  healthSeq?: number | null;
  fallbackSeq?: number | null;
}

export interface ProjectionFreshness {
  projection_state?: string;
  tail_behind?: boolean;
}

export interface ChannelEventRefreshPlan {
  conversation: boolean;
  summary: boolean;
}

const CHANNEL_CONVERSATION_ONLY_PREFIXES = [
  "channel.typing.",
  "channel.message.stream.",
  "channel.context_pack.",
  "channel.relay.",
  "channel.finding.",
] as const;

export const BOARD_REFRESH_PAGES = new Set<PageId>(["board", "project", "task", "triage"]);
export const MEASURE_REFRESH_PAGES = new Set<PageId>([
  "delivery",
  "goal-coverage",
  "delivery-trace",
  "delivery-graph",
  "behavior-loop",
]);

const OBSERVABILITY_SNAPSHOT_PAGES = new Set<PageId>([
  "observability",
  "events",
  "runs",
  "fanouts",
  "candidates",
  "workdirs",
  "skills",
  "archives",
]);

const LIGHT_SNAPSHOT_PAGES = new Set<PageId>([
  "project",
  "board",
  "task",
  "triage",
  "traces",
  "runtime",
  "settings",
  "diagnostics",
]);

export function isObservabilitySnapshotPage(page: PageId): boolean {
  return OBSERVABILITY_SNAPSHOT_PAGES.has(page);
}

export function snapshotLoadKindForPage(page: PageId): SnapshotLoadKind {
  if (OBSERVABILITY_SNAPSHOT_PAGES.has(page)) return "full";
  if (LIGHT_SNAPSHOT_PAGES.has(page)) return "light";
  return "none";
}

export function pageLoadsSnapshot(page: PageId): boolean {
  return snapshotLoadKindForPage(page) !== "none";
}

export function pageLoadsDeliveryFeatures(page: PageId): boolean {
  return MEASURE_REFRESH_PAGES.has(page);
}

export function pagePollsOperatorInbox(page: PageId): boolean {
  return page === "inbox";
}

export function pageLoadsChannels(page: PageId): boolean {
  return pageOwnsBootstrapResource(page, "channels.summary");
}

export function pageLoadsRecentEvents(page: PageId): boolean {
  return pageOwnsBootstrapResource(page, "events.recent");
}

export function pageResourcePlanForPage(page: PageId): PageResourcePlan {
  const foreground: BootstrapResource[] = ["project.health"];
  const snapshotKind = snapshotLoadKindForPage(page);
  if (snapshotKind === "light") foreground.push("project.snapshot.light");
  if (snapshotKind === "full") foreground.push("project.snapshot.full");
  if (pageLoadsDeliveryFeatures(page)) foreground.push("delivery.features");
  if (page === "channels") foreground.push("channels.summary");
  // Other consumers either own a scoped history endpoint or only need events
  // arriving after the SSE connection.
  if (page === "triage") foreground.push("events.recent");
  if (page === "inbox") foreground.push("operator.inbox");
  return { foreground };
}

export function pageOwnsBootstrapResource(
  page: PageId,
  resource: BootstrapResource,
): boolean {
  return pageResourcePlanForPage(page).foreground.includes(resource);
}

export function bootstrapEventCursor(sources: BootstrapCursorSources): number {
  if (sources.recentEventsSeq !== null && sources.recentEventsSeq !== undefined) {
    return validCursor(sources.recentEventsSeq);
  }
  return Math.max(
    validCursor(sources.snapshotSeq),
    validCursor(sources.healthSeq),
    validCursor(sources.fallbackSeq),
  );
}

export function projectionNeedsFresh(projection: ProjectionFreshness): boolean {
  return Boolean(
    projection.tail_behind
    || (projection.projection_state && projection.projection_state !== "ready"),
  );
}

export function channelEventRefreshPlan(eventType: string): ChannelEventRefreshPlan {
  if (!eventType.startsWith("channel.")) {
    return { conversation: false, summary: false };
  }
  return {
    conversation: true,
    summary: !CHANNEL_CONVERSATION_ONLY_PREFIXES.some((prefix) => (
      eventType.startsWith(prefix)
    )),
  };
}

function validCursor(value: number | null | undefined): number {
  const cursor = Number(value ?? 0);
  return Number.isInteger(cursor) && cursor >= 0 ? cursor : 0;
}
