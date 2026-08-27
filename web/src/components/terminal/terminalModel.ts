import type { TerminalSession } from "./types";

export interface TerminalTabSnapshot {
  activeId: string;
  openIds: string[];
}

export type TerminalScrollSource = "wheel" | "page_key";

export interface TerminalScrollCommand {
  type: "terminal.scroll";
  direction: "up" | "down";
  lines: number;
  source: TerminalScrollSource;
}

interface TerminalModifierState {
  altKey: boolean;
  ctrlKey: boolean;
  metaKey: boolean;
  shiftKey: boolean;
}

const TERMINAL_TAB_SCHEMA = "terminal-tabs.v1";
const MAX_PERSISTED_TABS = 32;
const TERMINAL_SCROLL_MAX_LINES = 1_000;
const TERMINAL_WHEEL_PIXELS_PER_ROW = 40;

export function activeTerminalSessions(sessions: TerminalSession[]): TerminalSession[] {
  return sessions.filter((session) => session.state === "active");
}

export function reconcileOpenTerminalTabs(
  openIds: string[],
  sessions: TerminalSession[],
): string[] {
  const known = new Set(sessions.map((session) => session.session_id));
  return openIds.filter((id) => known.has(id));
}

export function openTerminalTab(openIds: string[], sessionId: string): string[] {
  return openIds.includes(sessionId) ? openIds : [...openIds, sessionId];
}

export function closeTerminalTab(openIds: string[], sessionId: string): string[] {
  return openIds.filter((id) => id !== sessionId);
}

export function activeTerminalAfterClose(
  openIds: string[],
  closedId: string,
  activeId: string,
): string {
  if (activeId !== closedId) return activeId;
  const closedIndex = openIds.indexOf(closedId);
  const remaining = closeTerminalTab(openIds, closedId);
  if (closedIndex < 0 || remaining.length === 0) return "";
  return remaining[Math.min(closedIndex, remaining.length - 1)] ?? "";
}

export function parseTerminalTabSnapshot(raw: string | null): TerminalTabSnapshot | null {
  if (!raw) return null;
  try {
    const value = JSON.parse(raw) as Record<string, unknown>;
    if (value.schema_version !== TERMINAL_TAB_SCHEMA || !Array.isArray(value.open_ids)) {
      return null;
    }
    const openIds = [...new Set(
      value.open_ids.filter((item): item is string => typeof item === "string" && item.length > 0),
    )].slice(0, MAX_PERSISTED_TABS);
    const activeId = typeof value.active_id === "string" && openIds.includes(value.active_id)
      ? value.active_id
      : (openIds[0] ?? "");
    return { activeId, openIds };
  } catch {
    return null;
  }
}

export function serializeTerminalTabSnapshot(snapshot: TerminalTabSnapshot): string {
  return JSON.stringify({
    schema_version: TERMINAL_TAB_SCHEMA,
    open_ids: snapshot.openIds.slice(0, MAX_PERSISTED_TABS),
    active_id: snapshot.activeId,
  });
}

export function terminalReconnectDelay(attempt: number): number {
  return Math.min(5_000, 250 * (2 ** Math.max(0, Math.min(attempt, 5))));
}

export function terminalWheelDeltaRows(
  deltaY: number,
  deltaMode: number,
  viewportRows: number,
): number {
  if (!Number.isFinite(deltaY) || deltaY === 0) return 0;
  if (deltaMode === 1) return deltaY;
  if (deltaMode === 2) return deltaY * Math.max(1, Math.floor(viewportRows) - 1);
  return deltaY / TERMINAL_WHEEL_PIXELS_PER_ROW;
}

export function terminalScrollFromRows(
  rows: number,
  source: TerminalScrollSource,
): TerminalScrollCommand | null {
  if (!Number.isFinite(rows) || Math.abs(rows) < 0.5) return null;
  return {
    type: "terminal.scroll",
    direction: rows < 0 ? "up" : "down",
    lines: Math.min(TERMINAL_SCROLL_MAX_LINES, Math.max(1, Math.round(Math.abs(rows)))),
    source,
  };
}

export function terminalModifierBits(modifiers: TerminalModifierState): number {
  return (modifiers.shiftKey ? 1 : 0)
    | (modifiers.ctrlKey ? 2 : 0)
    | (modifiers.altKey ? 4 : 0)
    | (modifiers.metaKey ? 8 : 0);
}

export function acceptsTerminalFrame(
  lastSeq: number | null,
  hasFullFrame: boolean,
  next: { seq: number; full: boolean },
): boolean {
  if (!hasFullFrame && !next.full) return false;
  return lastSeq === null || next.seq === lastSeq + 1;
}
