import { expect, test } from "@playwright/test";

async function dismissWelcomeIfNeeded(page: import("@playwright/test").Page) {
  const wizard = page.getByTestId("welcome-wizard");
  const visible = await wizard.waitFor({ state: "visible", timeout: 3_000 })
    .then(() => true)
    .catch(() => false);
  if (visible) {
    await page.getByTestId("welcome-skip").click();
    await expect(wizard).toBeHidden();
  }
}

async function openNewCodexTerminal(
  drawer: import("@playwright/test").Locator,
) {
  await drawer.getByRole("button", { name: "New terminal" }).click();
  await drawer.getByRole("menuitem", { name: "New Codex terminal" }).click();
}

async function selectTerminalAction(
  drawer: import("@playwright/test").Locator,
  name: string,
) {
  await drawer.getByRole("button", { name: "Terminal actions" }).click();
  await drawer.getByRole("menu").getByText(name, { exact: true }).click();
}

test("Web Terminal keeps native dock tabs mounted, maximizes, reconnects, and stops explicitly", async ({ page }) => {
  page.on("pageerror", (error) => console.error(`[pageerror] ${error.stack ?? error.message}`));
  page.on("console", (message) => {
    if (message.type() === "error") console.error(`[console] ${message.text()}`);
  });
  const attachmentRequests: string[] = [];
  const renameRequests: string[] = [];
  page.on("request", (request) => {
    if (request.method() === "POST" && /\/(attachments|takeover)$/.test(request.url())) {
      attachmentRequests.push(request.url());
    }
    if (request.method() === "POST" && /\/rename$/.test(request.url())) {
      renameRequests.push(request.url());
    }
  });
  const actionToken = process.env.ZF_WEB_ACTION_TOKEN_FOR_TEST ?? "";
  const targetProjectId = process.env.ZF_WEB_TERMINAL_TARGET_PROJECT_ID ?? "";
  await page.addInitScript(({ token, projectId }) => {
    if (!window.localStorage.getItem("zf.themeMode")) {
      window.localStorage.setItem("zf.themeMode", "light");
    }
    if (token) window.localStorage.setItem("zf.webActionToken", token);
    if (projectId) window.localStorage.setItem("zf.activeProjectId", projectId);
  }, { token: actionToken, projectId: targetProjectId });
  await page.goto(targetProjectId
    ? `/?page=board&project=${encodeURIComponent(targetProjectId)}`
    : "/");
  await dismissWelcomeIfNeeded(page);
  if (targetProjectId) await expect(page.getByLabel("Project")).toHaveValue(targetProjectId);

  const refreshDashboard = page.getByRole("button", { name: "Refresh" });
  await expect(refreshDashboard.locator("svg")).toHaveCount(1);
  await expect(refreshDashboard).toHaveText("");
  await expect(refreshDashboard).toHaveClass(/topbar-refresh-button/);
  await expect(page.locator(".topbar-runtime-cluster").getByRole("button")).toHaveCount(1);
  await expect(page.locator(".status-pill.status-live")).toHaveText("synced");
  const terminalToggle = page.getByRole("button", { name: "Toggle Terminal" });
  await expect(terminalToggle).toBeInViewport();
  await expect(terminalToggle).toHaveClass(/topbar-terminal-launch/);
  await expect(page.locator(".topbar-tool-separator")).toHaveCount(1);
  await expect(page.getByRole("group", { name: "Workspace tools" })).toHaveCount(0);
  await expect(terminalToggle.locator("svg")).toHaveCount(1);
  await expect(terminalToggle).toHaveAttribute("aria-pressed", "false");
  await terminalToggle.click();
  await expect(terminalToggle).toHaveAttribute("aria-pressed", "true");
  const drawer = page.getByRole("complementary", { name: "Coding Agent Terminal" });
  await expect(drawer).toBeVisible();
  await expect(drawer.getByLabel("herdr 0.8.0")).toBeVisible();
  await expect(page.locator(".web-terminal-backdrop")).toHaveCount(0);
  await expect(page.locator(".web-terminal-layer")).toHaveCSS("pointer-events", "none");
  await expect(page.locator("html")).toHaveAttribute("data-theme", "light");
  await expect(drawer).toHaveAttribute("data-fullscreen", "true");
  const viewport = page.viewportSize();
  const initialFullscreenBox = await drawer.boundingBox();
  expect(initialFullscreenBox?.width).toBe(viewport?.width);
  expect(initialFullscreenBox?.height).toBe(viewport?.height);
  await expect(drawer).toHaveCSS("border-radius", "0px");
  await expect(drawer).toHaveCSS("box-shadow", "none");
  await drawer.getByRole("button", { name: "Exit full screen" }).click();
  await expect(drawer).toHaveAttribute("data-fullscreen", "false");
  await expect(page.locator(".workspace > .web-terminal-layer")).toHaveCount(1);
  await expect(page.locator(".web-terminal-layer")).toHaveCSS("position", "relative");
  await expect(drawer).toHaveCSS("border-left-width", "0px");
  await expect(drawer).toHaveCSS("border-right-width", "0px");
  await expect(drawer).toHaveCSS("border-bottom-width", "0px");
  await expect(drawer).toHaveCSS("border-radius", "0px");
  await expect(drawer).toHaveCSS("box-shadow", "none");
  const boardBox = await page.locator(".board-panel").boundingBox();
  const dockBox = await drawer.boundingBox();
  expect(dockBox?.x).toBe(boardBox?.x);
  expect(dockBox?.width).toBe(boardBox?.width);
  const lightDrawerBackground = await drawer.evaluate((element) => getComputedStyle(element).backgroundColor);

  await openNewCodexTerminal(drawer);
  const tabs = drawer.getByRole("tab");
  await expect(tabs).toHaveCount(1);
  await expect(tabs.nth(0)).toContainText("Codex 1");
  await expect(drawer.locator(".web-terminal-view")).toHaveAttribute("data-terminal-status", "controlling");
  await expect(drawer.locator(".web-terminal-view")).toHaveAttribute("data-terminal-theme", "light");
  await expect(drawer.locator(".xterm-rows")).toContainText("Coding agent ready");
  const terminalFrameColors = await drawer.locator(".web-terminal-xterm-host").evaluate((host) => {
    const xterm = host.querySelector<HTMLElement>(".xterm");
    const viewport = host.querySelector<HTMLElement>(".xterm-viewport");
    return {
      host: getComputedStyle(host).backgroundColor,
      xterm: xterm ? getComputedStyle(xterm).backgroundColor : "missing",
      viewport: viewport ? getComputedStyle(viewport).backgroundColor : "missing",
    };
  });
  expect(terminalFrameColors.xterm).toBe(terminalFrameColors.host);
  expect(terminalFrameColors.viewport).toBe(terminalFrameColors.host);

  const attachmentsBeforeRename = attachmentRequests.length;
  await selectTerminalAction(drawer, "Rename terminal");
  const renameInput = drawer.getByRole("textbox", { name: "Rename Codex 1" });
  await renameInput.fill("Review API");
  await renameInput.press("Enter");
  await expect(tabs.nth(0)).toContainText("Review API");
  await expect.poll(() => renameRequests.length).toBe(1);
  await page.waitForTimeout(250);
  expect(attachmentRequests).toHaveLength(attachmentsBeforeRename);

  await selectTerminalAction(drawer, "Observe");
  await expect(drawer.locator(".web-terminal-view")).toHaveAttribute("data-terminal-status", "observing");
  await selectTerminalAction(drawer, "Control");
  await expect(drawer.locator(".web-terminal-view")).toHaveAttribute("data-terminal-status", "controlling");
  await selectTerminalAction(drawer, "Take over control");
  await expect(drawer.locator(".web-terminal-view")).toHaveAttribute("data-terminal-status", "controlling");

  const firstInput = drawer.locator(".web-terminal-session-panel.is-active .xterm-helper-textarea");
  await firstInput.focus();
  await firstInput.pressSequentially("first-session");
  await expect(drawer.locator(".web-terminal-session-panel.is-active .xterm-rows"))
    .toContainText("first-session");
  const firstSessionId = await tabs.nth(0).getAttribute("data-session-id");
  expect(firstSessionId).toBeTruthy();

  await openNewCodexTerminal(drawer);
  await expect(tabs).toHaveCount(2);
  await expect(tabs.nth(1)).toContainText("Codex 2");
  await expect(drawer.locator(".web-terminal-session-panel")).toHaveCount(2);
  await expect(drawer.locator(".web-terminal-xterm-host")).toHaveCount(2);
  await expect(drawer.locator(".web-terminal-session-panel.is-active .web-terminal-view"))
    .toHaveAttribute("data-terminal-status", "controlling");
  const secondSessionId = await tabs.nth(1).getAttribute("data-session-id");
  expect(secondSessionId).toBeTruthy();
  expect(secondSessionId).not.toBe(firstSessionId);

  const secondInput = drawer.locator(".web-terminal-session-panel.is-active .xterm-helper-textarea");
  await secondInput.focus();
  await secondInput.pressSequentially("second-session");
  await expect(drawer.locator(".web-terminal-session-panel.is-active .xterm-rows"))
    .toContainText("second-session");

  const attachmentsBeforeSwitch = attachmentRequests.length;
  await tabs.nth(0).click();
  await expect(tabs.nth(0)).toHaveAttribute("aria-selected", "true");
  await expect(drawer.locator(".web-terminal-session-panel.is-active"))
    .toHaveAttribute("data-session-id", firstSessionId ?? "");
  await expect(drawer.locator(".web-terminal-session-panel.is-active .xterm-rows"))
    .toContainText("first-session");
  await tabs.nth(1).click();
  await expect(tabs.nth(1)).toHaveAttribute("aria-selected", "true");
  await expect(drawer.locator(".web-terminal-session-panel.is-active"))
    .toHaveAttribute("data-session-id", secondSessionId ?? "");
  await expect(drawer.locator(".web-terminal-session-panel.is-active .xterm-rows"))
    .toContainText("second-session");
  await page.waitForTimeout(250);
  expect(attachmentRequests).toHaveLength(attachmentsBeforeSwitch);

  const resizeHandle = drawer.getByRole("separator", { name: "Resize terminal dock" });
  const heightBeforeResize = (await drawer.boundingBox())?.height ?? 0;
  await resizeHandle.focus();
  await resizeHandle.press("ArrowUp");
  await expect.poll(async () => (await drawer.boundingBox())?.height ?? 0)
    .toBeGreaterThan(heightBeforeResize);
  const resizedHeight = (await drawer.boundingBox())?.height ?? 0;

  await drawer.getByRole("button", { name: "Enter full screen" }).click();
  await expect(drawer).toHaveAttribute("data-fullscreen", "true");
  const fullscreenBox = await drawer.boundingBox();
  expect(fullscreenBox?.width).toBe(viewport?.width);
  expect(fullscreenBox?.height).toBe(viewport?.height);
  await page.keyboard.press("Escape");
  await expect(drawer).toHaveAttribute("data-fullscreen", "false");

  await drawer.getByRole("button", { name: "Close terminal dock" }).click();
  await expect(drawer).toBeHidden();
  await expect(terminalToggle).toHaveAttribute("aria-pressed", "false");

  // Closing the dock detaches only. Reopen restores the browser tab set and
  // dock height, while Herdr remains the durable PTY/session owner.
  await terminalToggle.click();
  await expect(drawer).toHaveAttribute("data-fullscreen", "true");
  await drawer.getByRole("button", { name: "Exit full screen" }).click();
  await expect(tabs).toHaveCount(2);
  await expect(tabs.nth(0)).toContainText("Review API");
  await expect(tabs.nth(1)).toContainText("Codex 2");
  await expect(tabs.nth(1)).toHaveAttribute("aria-selected", "true");
  await expect(drawer.locator(".web-terminal-session-panel.is-active .xterm-rows"))
    .toContainText("second-session");
  await expect.poll(async () => (await drawer.boundingBox())?.height ?? 0)
    .toBeCloseTo(resizedHeight, 0);

  await page.evaluate(() => window.localStorage.setItem("zf.themeMode", "dark"));
  await page.reload();
  await dismissWelcomeIfNeeded(page);
  await page.getByRole("button", { name: "Toggle Terminal" }).click();
  await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
  await expect(drawer.locator(".web-terminal-session-panel.is-active .web-terminal-view"))
    .toHaveAttribute("data-terminal-theme", "dark");
  const darkDrawerBackground = await drawer.evaluate((element) => getComputedStyle(element).backgroundColor);
  expect(darkDrawerBackground).not.toBe(lightDrawerBackground);
  await expect(tabs).toHaveCount(2);
  await expect(tabs.nth(1)).toHaveAttribute("aria-selected", "true");

  await selectTerminalAction(drawer, "Stop CLI");
  await expect(tabs.nth(1).locator(".web-terminal-state-dot")).toHaveClass(/is-stopped/);
  await expect(drawer.locator(".web-terminal-connection-notice")).toHaveCount(0);
  await tabs.nth(0).click();
  await selectTerminalAction(drawer, "Stop CLI");
  await expect(tabs.nth(0).locator(".web-terminal-state-dot")).toHaveClass(/is-stopped/);
  await expect(drawer.locator(".web-terminal-connection-notice")).toHaveCount(0);
});
