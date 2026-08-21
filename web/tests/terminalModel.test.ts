import {
  acceptsTerminalFrame,
  activeTerminalAfterClose,
  closeTerminalTab,
  openTerminalTab,
  parseTerminalTabSnapshot,
  reconcileOpenTerminalTabs,
  serializeTerminalTabSnapshot,
  terminalReconnectDelay,
} from "../src/components/terminal/terminalModel.js";
import type { TerminalSession } from "../src/components/terminal/types.js";

function assert(condition: unknown, message: string): void {
  if (!condition) throw new Error(message);
}

function session(id: string, state = "active"): TerminalSession {
  return {
    session_id: id,
    slot: id,
    title: id,
    provider: "codex",
    provider_kind: "codex",
    project_id: "project-a",
    state,
    generation: 1,
    created_at: "2026-08-20T00:00:00Z",
    updated_at: "2026-08-20T00:00:00Z",
    diagnostics: [],
  };
}

function testTabCloseOnlyDetaches(): void {
  const sessions = [session("a"), session("b")];
  const open = openTerminalTab(["a"], "b");
  const closed = closeTerminalTab(open, "a");

  assert(closed.length === 1 && closed[0] === "b", "closing a tab should remove only its attachment");
  assert(sessions[0].state === "active", "tab close must not mutate the terminal session lifecycle");
}

function testTabsReconcileAgainstProjectSessions(): void {
  const reconciled = reconcileOpenTerminalTabs(["a", "missing", "b"], [session("a"), session("b", "stopped")]);
  assert(reconciled.join(",") === "a,b", "known stopped session remains visible while unknown ids are removed");
}

function testActiveTabMovesToItsNearestNeighbor(): void {
  assert(activeTerminalAfterClose(["a", "b", "c"], "b", "b") === "c", "middle tab should select its right neighbor");
  assert(activeTerminalAfterClose(["a", "b"], "b", "b") === "a", "last tab should select its left neighbor");
  assert(activeTerminalAfterClose(["a", "b"], "a", "b") === "b", "closing an inactive tab must keep the active tab");
}

function testTabSnapshotIsVersionedAndRejectsInvalidStorage(): void {
  const serialized = serializeTerminalTabSnapshot({ activeId: "b", openIds: ["a", "b"] });
  const restored = parseTerminalTabSnapshot(serialized);

  assert(restored?.activeId === "b", "active terminal should survive a drawer remount");
  assert(restored?.openIds.join(",") === "a,b", "open terminal tabs should survive a drawer remount");
  assert(parseTerminalTabSnapshot('{"schema_version":"old","open_ids":["a"]}') === null, "stale tab storage must fail closed");
  assert(parseTerminalTabSnapshot("not-json") === null, "invalid tab storage must fail closed");
}

function testReconnectIsBounded(): void {
  assert(terminalReconnectDelay(0) === 250, "first retry should be prompt");
  assert(terminalReconnectDelay(99) === 5_000, "retry delay must stay bounded");
}

function testFramesRequireFullBaselineAndMonotonicSequence(): void {
  assert(!acceptsTerminalFrame(null, false, { seq: 1, full: false }), "delta cannot establish a baseline");
  assert(acceptsTerminalFrame(null, false, { seq: 1, full: true }), "full frame establishes baseline");
  assert(!acceptsTerminalFrame(2, true, { seq: 2, full: false }), "duplicate sequence must be rejected");
  assert(!acceptsTerminalFrame(2, true, { seq: 4, full: false }), "sequence gaps must be rejected");
  assert(acceptsTerminalFrame(2, true, { seq: 3, full: false }), "new delta should be accepted");
}

testTabCloseOnlyDetaches();
testTabsReconcileAgainstProjectSessions();
testActiveTabMovesToItsNearestNeighbor();
testTabSnapshotIsVersionedAndRejectsInvalidStorage();
testReconnectIsBounded();
testFramesRequireFullBaselineAndMonotonicSequence();
