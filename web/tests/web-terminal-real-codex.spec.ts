import { expect, test } from "@playwright/test";
import type { Page } from "@playwright/test";

const realProviderEnabled = process.env.ZF_WEB_REAL_CODEX_E2E === "1";

function captureTerminalFrames(page: Page): (sessionId: string) => string {
  const frames = new Map<string, { pendingFull: boolean; raw: string }>();
  page.on("websocket", (socket) => {
    const match = decodeURIComponent(new URL(socket.url()).pathname)
      .match(/\/terminal-sessions\/([^/]+)\/(?:observe|control)$/);
    if (!match) return;
    const sessionId = match[1];
    const state = frames.get(sessionId) ?? { pendingFull: false, raw: "" };
    frames.set(sessionId, state);
    socket.on("framereceived", ({ payload }) => {
      if (typeof payload === "string") {
        try {
          const message = JSON.parse(payload) as { full?: boolean; type?: string };
          if (message.type === "terminal.frame") state.pendingFull = message.full === true;
        } catch {
          // Terminal screen content is carried in the following binary frame.
        }
        return;
      }
      const chunk = payload.toString("utf8");
      state.raw = state.pendingFull ? chunk : `${state.raw}${chunk}`.slice(-250_000);
      state.pendingFull = false;
    });
  });
  return (sessionId: string) => (frames.get(sessionId)?.raw ?? "")
    .replace(/\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)/g, "")
    .replace(/\x1b(?:\[[0-?]*[ -/]*[@-~]|[@-_])/g, "")
    .replace(/\r/g, "\n");
}

function markerRange(text: string): { count: number; max: number; min: number } {
  const values = [...text.matchAll(/ZFSCROLL-(\d{3})/g)]
    .map((match) => Number(match[1]));
  const unique = [...new Set(values)];
  return {
    count: unique.length,
    max: unique.length ? Math.max(...unique) : 0,
    min: unique.length ? Math.min(...unique) : 0,
  };
}

test.skip(!realProviderEnabled, "set ZF_WEB_REAL_CODEX_E2E=1 for the explicit host/provider tier");

test("Dashboard authorization starts and scrolls the installed Codex TUI through real Herdr", async ({ page }) => {
  test.setTimeout(240_000);
  const actionToken = process.env.ZF_WEB_ACTION_TOKEN_FOR_TEST ?? "";
  const passcode = process.env.ZF_WEB_REAL_CODEX_PASSCODE;
  const targetProjectId = process.env.ZF_WEB_TERMINAL_TARGET_PROJECT_ID ?? "";
  const terminalText = captureTerminalFrames(page);
  if (!actionToken && !passcode) {
    throw new Error("ZF_WEB_ACTION_TOKEN_FOR_TEST or ZF_WEB_REAL_CODEX_PASSCODE is required");
  }

  await page.addInitScript(({ token, projectId }) => {
    if (token) window.localStorage.setItem("zf.webActionToken", token);
    if (projectId) window.localStorage.setItem("zf.activeProjectId", projectId);
  }, { token: actionToken, projectId: targetProjectId });
  await page.goto(targetProjectId
    ? `/?page=board&project=${encodeURIComponent(targetProjectId)}`
    : "/");
  const wizard = page.getByTestId("welcome-wizard");
  if (await wizard.isVisible().catch(() => false)) await page.getByTestId("welcome-skip").click();
  if (targetProjectId) await expect(page.getByLabel("Project")).toHaveValue(targetProjectId);

  if (!actionToken && passcode) {
    const boardLock = page.locator(".board-action-notice");
    await expect(boardLock.getByText("board actions: passcode needed", { exact: true })).toBeVisible();
    await boardLock.getByLabel("Web passcode").fill(passcode);
    await boardLock.getByRole("button", { name: "Unlock" }).click();
    await expect(boardLock).toBeHidden();
  }

  await page.getByRole("button", { name: "Toggle Terminal" }).click();
  const drawer = page.getByRole("complementary", { name: "Coding Agent Terminal" });
  await drawer.getByRole("button", { name: "New terminal" }).click();
  const createResponsePromise = page.waitForResponse((response) => (
    response.request().method() === "POST"
    && /\/terminal-sessions$/.test(new URL(response.url()).pathname)
  ));
  await drawer.getByRole("menuitem", { name: "New Codex terminal" }).click();
  const createResponse = await createResponsePromise;
  const createBody = await createResponse.json() as { session: { session_id: string } };
  expect(createResponse.ok()).toBe(true);
  const sessionId = createBody.session.session_id;

  await expect(drawer.getByRole("tab")).toHaveCount(1, { timeout: 75_000 });
  await expect(drawer.locator(".web-terminal-view"))
    .toHaveAttribute("data-terminal-status", "controlling", { timeout: 30_000 });
  const input = drawer.locator(".xterm-helper-textarea");
  await input.focus();
  const startupDeadline = Date.now() + 75_000;
  let handledTrust = false;
  let handledHooks = false;
  let handledUpdate = false;
  let readySince = 0;
  while (Date.now() < startupDeadline) {
    const startup = terminalText(sessionId);
    if (!handledUpdate && /update available/i.test(startup)) {
      await input.press("ArrowDown");
      await input.press("ArrowDown");
      await input.press("Enter");
      handledUpdate = true;
      readySince = 0;
      await page.waitForTimeout(750);
      continue;
    }
    if (!handledTrust && /do you trust|trust the contents/i.test(startup)) {
      await input.press("Enter");
      handledTrust = true;
      readySince = 0;
      await page.waitForTimeout(750);
      continue;
    }
    if (!handledHooks && /hooks need review/i.test(startup)) {
      await input.press("ArrowDown");
      await input.press("Enter");
      handledHooks = true;
      readySince = 0;
      await page.waitForTimeout(750);
      continue;
    }
    const normalized = startup.toLowerCase();
    const readyIndex = Math.max(
      normalized.lastIndexOf("openai codex"),
      normalized.lastIndexOf("ask codex to do anything"),
    );
    const lastGateIndex = Math.max(
      normalized.lastIndexOf("update available"),
      normalized.lastIndexOf("do you trust"),
      normalized.lastIndexOf("trust the contents"),
      normalized.lastIndexOf("hooks need review"),
      normalized.lastIndexOf("model: loading"),
    );
    const ready = readyIndex >= 0 && readyIndex > lastGateIndex;
    if (ready) {
      readySince ||= Date.now();
      if (Date.now() - readySince >= 1_500) break;
    } else {
      readySince = 0;
    }
    await page.waitForTimeout(250);
  }
  expect(readySince, "Codex TUI should remain ready after startup gates").toBeGreaterThan(0);
  expect(Date.now() - readySince, "Codex ready state should be stable").toBeGreaterThanOrEqual(1_500);
  expect(terminalText(sessionId)).not.toContain("[Fake Herdr]");

  await input.pressSequentially("/status");
  await input.press("Enter");
  await expect.poll(() => terminalText(sessionId), { timeout: 30_000 })
    .toMatch(/session id|model|account/i);

  await input.press("Escape");
  await input.focus();
  await page.keyboard.insertText("中文输入验证 ZF-INPUT-✓");
  await expect(drawer.locator(".xterm-rows")).toContainText("中文输入验证 ZF-INPUT-✓");
  await input.press("Control+U");
  await page.keyboard.insertText(
    "Output exactly 80 lines, one per line, labeled ZFSCROLL-001 through ZFSCROLL-080, with no other text.",
  );
  await input.press("Enter");
  await expect.poll(() => terminalText(sessionId), { timeout: 90_000 })
    .toContain("ZFSCROLL-080");
  await page.waitForTimeout(1_500);

  const terminalRows = drawer.locator(".xterm-rows");
  const bottomRange = markerRange(await terminalRows.innerText());
  expect(bottomRange.count, "live Codex viewport should contain numbered output").toBeGreaterThan(0);
  const screen = drawer.locator(".xterm-screen");
  await screen.hover();
  await page.mouse.wheel(0, -1_200);
  await expect.poll(async () => {
    const current = markerRange(await terminalRows.innerText());
    return current.min < bottomRange.min || current.max < bottomRange.max;
  }, { timeout: 15_000 }).toBe(true);
  const earlierRange = markerRange(await terminalRows.innerText());
  expect(
    earlierRange.min < bottomRange.min || earlierRange.max < bottomRange.max,
    `wheel up should reveal older Herdr history: bottom=${JSON.stringify(bottomRange)} earlier=${JSON.stringify(earlierRange)}`,
  ).toBe(true);

  await page.mouse.wheel(0, 1_200);
  await expect.poll(async () => markerRange(await terminalRows.innerText()).max, { timeout: 15_000 })
    .toBeGreaterThanOrEqual(bottomRange.max);

  const projection = await page.evaluate(async (projectId) => {
    const response = await fetch(`/api/projects/${encodeURIComponent(projectId || "default")}/terminal-sessions`);
    return response.json() as Promise<{ sessions: Array<{ provider: string; state: string }> }>;
  }, targetProjectId);
  expect(projection.sessions).toContainEqual(expect.objectContaining({ provider: "codex", state: "active" }));

  await drawer.getByRole("button", { name: "Terminal actions" }).click();
  await drawer.getByRole("menuitem", { name: "Stop CLI" }).click();
  await expect(drawer.locator(".web-terminal-state-dot")).toHaveClass(/is-stopped/);
  await expect(drawer.locator(".web-terminal-connection-notice")).toHaveCount(0);
});
