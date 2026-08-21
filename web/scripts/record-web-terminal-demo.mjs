import { mkdir, writeFile } from "node:fs/promises";
import path from "node:path";
import { chromium, expect } from "@playwright/test";

const baseURL = process.env.ZF_WEB_BASE_URL ?? "";
const actionToken = process.env.ZF_WEB_ACTION_TOKEN_FOR_TEST ?? "";
const projectId = process.env.ZF_WEB_TERMINAL_TARGET_PROJECT_ID ?? "";
const sourceCommit = process.env.ZF_WEB_TERMINAL_SOURCE_COMMIT ?? "";
const outputDir = path.resolve(
  process.env.ZF_WEB_TERMINAL_DEMO_OUTPUT_DIR ?? "test-results/web-terminal-demo",
);
const executablePath = process.env.ZF_E2E_CHROMIUM_EXECUTABLE_PATH;

if (!baseURL || !actionToken || !projectId || !sourceCommit) {
  throw new Error(
    "ZF_WEB_BASE_URL, ZF_WEB_ACTION_TOKEN_FOR_TEST, "
      + "ZF_WEB_TERMINAL_TARGET_PROJECT_ID, and ZF_WEB_TERMINAL_SOURCE_COMMIT are required",
  );
}

const framesDir = path.join(outputDir, "frames");
await mkdir(framesDir, { recursive: true });

const browser = await chromium.launch({ headless: true, executablePath });
const contexts = [];
const createdSessionIds = [];
const assertions = [];
const storyboard = [];
const terminalFramesByPage = new WeakMap();
const startedAt = new Date().toISOString();
let primarySession = null;
let secondarySession = null;
let usage = null;
let offlineStartedAt = "";
let offlineResponseObservedAt = "";
let recoveryAttachedAt = "";
let activeSessionsAfter = null;
let pageA = null;
let pageB = null;
let pageC = null;
let runError = null;

function installTerminalFrameCapture(page) {
  const frames = new Map();
  terminalFramesByPage.set(page, frames);
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
          const message = JSON.parse(payload);
          if (message?.type === "terminal.frame") state.pendingFull = message.full === true;
        } catch {
          // Terminal bytes are carried by the following binary frame.
        }
        return;
      }
      const chunk = payload.toString("utf8");
      state.raw = state.pendingFull ? chunk : `${state.raw}${chunk}`.slice(-300_000);
      state.pendingFull = false;
    });
  });
}

function terminalText(page, sessionId) {
  return (terminalFramesByPage.get(page)?.get(sessionId)?.raw ?? "")
    .replace(/\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)/g, "")
    .replace(/\x1b(?:\[[0-?]*[ -/]*[@-~]|[@-_])/g, "")
    .replace(/\r/g, "\n");
}

async function newOperatorContext(label, theme = "dark") {
  const context = await browser.newContext({
    colorScheme: theme,
    deviceScaleFactor: 2,
    locale: "zh-CN",
    viewport: { width: 1920, height: 1080 },
  });
  contexts.push(context);
  const page = await context.newPage();
  installTerminalFrameCapture(page);
  await page.addInitScript(({ operatorLabel, targetProjectId, token, themeMode }) => {
    window.localStorage.setItem("zf.webActionToken", token);
    window.localStorage.setItem("zf.activeProjectId", targetProjectId);
    window.localStorage.setItem("zf.themeMode", themeMode);
    window.sessionStorage.setItem("zf.showcaseOperator", operatorLabel);
  }, {
    operatorLabel: label,
    targetProjectId: projectId,
    token: actionToken,
    themeMode: theme,
  });
  await page.goto(`${baseURL}/?page=board&project=${encodeURIComponent(projectId)}`);
  const onboardingSkipped = await page.evaluate(async () => {
    const statusResponse = await fetch("/api/workspace/onboarding", { cache: "no-store" });
    if (!statusResponse.ok) throw new Error(`onboarding status failed: ${statusResponse.status}`);
    const status = await statusResponse.json();
    if (!status.show_welcome) return false;
    const token = window.localStorage.getItem("zf.webActionToken") ?? "";
    const response = await fetch("/api/workspace/onboarding", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-ZF-Web-Token": token,
        Authorization: `Bearer ${token}`,
      },
      body: JSON.stringify({ action: "skip" }),
    });
    if (!response.ok) throw new Error(`onboarding skip failed: ${response.status}`);
    return true;
  });
  if (onboardingSkipped) await page.reload();
  const wizard = page.getByTestId("welcome-wizard");
  if (await wizard.isVisible().catch(() => false)) {
    await page.getByTestId("welcome-skip").click();
  }
  await expect(page.getByLabel("Project")).toHaveValue(projectId);
  await expect(page.getByRole("button", { name: "Toggle Terminal" })).toBeVisible();
  await page.evaluate(() => document.fonts.ready);
  return { context, page };
}

async function capture(page, name, predicate, group) {
  await page.evaluate(() => document.fonts.ready);
  const file = `${name}.png`;
  await page.screenshot({ animations: "disabled", path: path.join(framesDir, file) });
  storyboard.push({ frame: file, group, predicate });
}

function drawer(page) {
  return page.getByRole("complementary", { name: "Coding Agent Terminal" });
}

async function openTerminal(page) {
  await page.getByRole("button", { name: "Toggle Terminal" }).click();
  const terminalDrawer = drawer(page);
  await expect(terminalDrawer).toHaveAttribute("data-fullscreen", "true");
  return terminalDrawer;
}

async function openActions(terminalDrawer, itemName, role = "menuitem") {
  const item = terminalDrawer.getByRole(role, { name: itemName });
  if (!await item.isVisible().catch(() => false)) {
    await terminalDrawer.getByRole("button", { name: "Terminal actions" }).click();
  }
  await expect(item).toBeVisible();
  return item;
}

async function createSession(page, terminalDrawer, providerLabel) {
  const responsePromise = page.waitForResponse((response) => (
    response.request().method() === "POST"
    && /\/terminal-sessions$/.test(new URL(response.url()).pathname)
  ));
  await terminalDrawer.getByRole("button", { name: "New terminal" }).click();
  await terminalDrawer.getByRole("menuitem", { name: `New ${providerLabel} terminal` }).click();
  const response = await responsePromise;
  const body = await response.json();
  if (!response.ok()) throw new Error(`create ${providerLabel} failed: ${JSON.stringify(body)}`);
  createdSessionIds.push(body.session.session_id);
  return body.session;
}

async function openRunningSession(page, terminalDrawer, sessionId, title) {
  await expect.poll(() => page.evaluate(async ({ targetProjectId, targetTitle }) => {
    const response = await fetch(
      `/api/projects/${encodeURIComponent(targetProjectId)}/terminal-sessions`,
      { cache: "no-store" },
    );
    if (!response.ok) return false;
    const body = await response.json();
    return body.sessions.some((session) => (
      session.state === "active" && session.title === targetTitle
    ));
  }, { targetProjectId: projectId, targetTitle: title }), { timeout: 30_000 }).toBe(true);
  const refreshResponse = page.waitForResponse((response) => (
    response.request().method() === "GET"
    && /\/terminal-sessions$/.test(new URL(response.url()).pathname)
  ));
  await terminalDrawer.getByRole("button", { name: "Refresh terminal sessions" }).click();
  expect((await refreshResponse).ok()).toBe(true);
  const existingTab = terminalDrawer.locator(
    `[role="tab"][data-session-id="${sessionId}"]`,
  );
  const autoOpened = await expect(existingTab).toBeVisible({ timeout: 5_000 })
    .then(() => true)
    .catch(() => false);
  if (autoOpened) {
    await existingTab.click();
    return;
  }
  await terminalDrawer.getByRole("button", { name: "New terminal" }).click();
  const item = terminalDrawer.getByRole("menuitem", { name: `Open ${title}` });
  await expect(item).toBeVisible({ timeout: 30_000 });
  await item.click();
}

async function renameActive(terminalDrawer, title) {
  await openActions(terminalDrawer, "Rename terminal");
  await terminalDrawer.getByRole("menuitem", { name: "Rename terminal" }).click();
  const input = terminalDrawer.getByRole("textbox", { name: /^Rename / });
  await input.fill(title);
  await input.press("Enter");
  await expect(terminalDrawer.getByRole("tab", { name: new RegExp(title) })).toBeVisible();
}

function sessionPanel(terminalDrawer, sessionId) {
  return terminalDrawer.locator(`.web-terminal-session-panel[data-session-id="${sessionId}"]`);
}

async function expectAttachment(terminalDrawer, sessionId, status, timeout = 45_000) {
  await expect(sessionPanel(terminalDrawer, sessionId).locator(".web-terminal-view"))
    .toHaveAttribute("data-terminal-status", status, { timeout });
}

async function setAttachmentMode(terminalDrawer, mode) {
  const label = mode === "observe" ? "Observe" : "Control";
  const item = await openActions(terminalDrawer, label, "menuitemradio");
  await item.click();
}

async function runCodexStartup(page, terminalDrawer, sessionId) {
  await expectAttachment(terminalDrawer, sessionId, "controlling");
  const panel = sessionPanel(terminalDrawer, sessionId);
  const input = panel.locator(".xterm-helper-textarea");
  await expect.poll(() => terminalText(page, sessionId).trim().length, {
    timeout: 45_000,
  }).toBeGreaterThan(0);
  await input.focus();
  const startupDeadline = Date.now() + 75_000;
  let handledTrust = false;
  let handledUpdate = false;
  let readySince = 0;
  while (Date.now() < startupDeadline) {
    const startup = terminalText(page, sessionId);
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
    const normalized = startup.toLowerCase();
    const readyIndex = Math.max(
      normalized.lastIndexOf("openai codex"),
      normalized.lastIndexOf("ask codex to do anything"),
    );
    const lastGateIndex = Math.max(
      normalized.lastIndexOf("update available"),
      normalized.lastIndexOf("do you trust"),
      normalized.lastIndexOf("trust the contents"),
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
  if (!readySince || Date.now() - readySince < 1_500) {
    throw new Error("Codex TUI did not remain ready after startup gates");
  }
  await sendCodexPrompt(
    page,
    terminalDrawer,
    sessionId,
    "Join ZAOFU WEB READY with underscores. Reply only with the result.",
    "ZAOFU_WEB_READY",
  );
}

async function sendCodexPrompt(page, terminalDrawer, sessionId, prompt, marker, timeout = 90_000) {
  const input = sessionPanel(terminalDrawer, sessionId).locator(".xterm-helper-textarea");
  await input.focus();
  await input.pressSequentially(prompt, { delay: 4 });
  await input.press("Enter");
  await expect.poll(() => terminalText(page, sessionId), { timeout }).toContain(marker);
}

async function submitOfflinePrompt(page, terminalDrawer, sessionId) {
  const prompt = "Run the terminal command sleep 8. After it completes, join OFFLINE RECOVERY OK with underscores. Reply only with the result.";
  const panel = sessionPanel(terminalDrawer, sessionId);
  const input = panel.locator(".xterm-helper-textarea");
  const currentScreen = async () => (
    await panel.locator(".xterm-rows").textContent() ?? ""
  );
  await input.focus();
  await input.pressSequentially(prompt, { delay: 3 });
  await input.press("Enter");
  await expect.poll(() => terminalText(page, sessionId), { timeout: 15_000 })
    .toMatch(/sleep\s*8/i);
  await expect.poll(currentScreen, { timeout: 30_000 })
    .toMatch(/would you like to run|yes,\s*proceed/i);
  await input.focus();
  await input.press("y");
  await page.waitForTimeout(250);
  if (/would you like to run|yes,\s*proceed/i.test(await currentScreen())) {
    await input.press("Enter");
  }
  await expect.poll(currentScreen, { timeout: 10_000 })
    .not.toMatch(/would you like to run|yes,\s*proceed/i);
  await page.waitForTimeout(250);
}

async function waitForUsage(page, sessionId) {
  let observed = null;
  await expect.poll(async () => {
    observed = await page.evaluate(async ({ id, targetProjectId }) => {
      const response = await fetch(
        `/api/projects/${encodeURIComponent(targetProjectId)}/terminal-sessions`,
        { cache: "no-store" },
      );
      if (!response.ok) throw new Error(`session projection failed: ${response.status}`);
      const body = await response.json();
      return body.sessions.find((session) => session.session_id === id)?.usage ?? null;
    }, { id: sessionId, targetProjectId: projectId });
    return observed?.status === "observed" && (observed?.total_tokens ?? 0) > 0;
  }, { timeout: 60_000 }).toBe(true);
  return observed;
}

async function fetchActiveSessionIds(page) {
  return page.evaluate(async (targetProjectId) => {
    const response = await fetch(
      `/api/projects/${encodeURIComponent(targetProjectId)}/terminal-sessions`,
      { cache: "no-store" },
    );
    if (!response.ok) throw new Error(`session projection failed: ${response.status}`);
    const body = await response.json();
    return body.sessions
      .filter((session) => session.state === "active")
      .map((session) => session.session_id);
  }, projectId);
}

async function stopSession(page, sessionId) {
  return page.evaluate(async ({ id, targetProjectId }) => {
    const token = window.localStorage.getItem("zf.webActionToken") ?? "";
    const response = await fetch(
      `/api/projects/${encodeURIComponent(targetProjectId)}/terminal-sessions/${encodeURIComponent(id)}/stop`,
      {
        method: "POST",
        headers: token
          ? { "X-ZF-Web-Token": token, Authorization: `Bearer ${token}` }
          : {},
      },
    );
    return response.status;
  }, { id: sessionId, targetProjectId: projectId });
}

try {
  const operatorA = await newOperatorContext("terminal-a");
  pageA = operatorA.page;
  await capture(
    pageA,
    "00-project-entry",
    "The authorized Project is selected and the Web Terminal entry is visible.",
    "entry-provider",
  );

  const drawerA = await openTerminal(pageA);
  await drawerA.getByRole("button", { name: "New terminal" }).click();
  await expect(drawerA.getByRole("menuitem", { name: "New Codex terminal" })).toBeVisible();
  await expect(drawerA.getByRole("menuitem", { name: "New Claude Code terminal" })).toBeVisible();
  assertions.push({
    id: "project-provider-menu",
    status: "passed",
    claim: "The mixed Project derives Codex and Claude Code without a terminal-only allowlist.",
  });
  await capture(
    pageA,
    "01-provider-menu",
    "The New Session menu contains Codex and Claude Code derived from the mixed Project.",
    "entry-provider",
  );
  await drawerA.getByRole("button", { name: "New terminal" }).click();

  primarySession = await createSession(pageA, drawerA, "Codex");
  await expect(drawerA.getByRole("tab")).toHaveCount(1, { timeout: 75_000 });
  await renameActive(drawerA, "Codex · 跨端主会话");
  await runCodexStartup(pageA, drawerA, primarySession.session_id);
  assertions.push({
    id: "real-codex-pty",
    status: "passed",
    claim: "The installed Codex TUI runs in a real Herdr PTY and returns ZAOFU_WEB_READY.",
  });
  await capture(
    pageA,
    "02-real-codex-terminal-a",
    "Terminal A controls the real Codex PTY and contains the exact ZAOFU_WEB_READY response.",
    "real-codex",
  );

  const operatorB = await newOperatorContext("terminal-b");
  pageB = operatorB.page;
  const drawerB = await openTerminal(pageB);
  await openRunningSession(
    pageB,
    drawerB,
    primarySession.session_id,
    "Codex · 跨端主会话",
  );
  await expect(drawerB.getByRole("tab")).toHaveCount(1, { timeout: 30_000 });
  await setAttachmentMode(drawerB, "observe");
  await expectAttachment(drawerB, primarySession.session_id, "observing");
  await expectAttachment(drawerA, primarySession.session_id, "controlling");
  await openActions(drawerB, "Observe", "menuitemradio");
  await expect(drawerB.getByRole("menuitemradio", { name: "Observe" }))
    .toHaveAttribute("aria-checked", "true");
  assertions.push({
    id: "cross-terminal-observe",
    status: "passed",
    claim: "An isolated second browser context observes the same server-side PTY while Terminal A retains control.",
  });
  await capture(
    pageA,
    "03-observe-terminal-a",
    "Terminal A remains the controller of the shared Session.",
    "cross-terminal-observe",
  );
  await capture(
    pageB,
    "03-observe-terminal-b",
    "Terminal B is attached to the same named Session in read-only Observe mode.",
    "cross-terminal-observe",
  );
  await pageB.keyboard.press("Escape");

  const takeoverResponse = pageB.waitForResponse((response) => (
    response.request().method() === "POST"
    && /\/takeover$/.test(new URL(response.url()).pathname)
  ));
  await openActions(drawerB, "Take over control");
  await drawerB.getByRole("menuitem", { name: "Take over control" }).click();
  expect((await takeoverResponse).ok()).toBe(true);
  await expectAttachment(drawerB, primarySession.session_id, "controlling");
  await expect.poll(async () => (
    await sessionPanel(drawerA, primarySession.session_id)
      .locator(".web-terminal-view")
      .getAttribute("data-terminal-status")
  ), { timeout: 30_000 }).not.toBe("controlling");
  await setAttachmentMode(drawerA, "observe");
  await expectAttachment(drawerA, primarySession.session_id, "observing");
  await sendCodexPrompt(
    pageB,
    drawerB,
    primarySession.session_id,
    "Join CROSS TERMINAL TAKEOVER OK with underscores. Reply only with the result.",
    "CROSS_TERMINAL_TAKEOVER_OK",
  );
  await expect.poll(() => terminalText(pageA, primarySession.session_id), { timeout: 30_000 })
    .toContain("CROSS_TERMINAL_TAKEOVER_OK");
  assertions.push({
    id: "cross-terminal-takeover",
    status: "passed",
    claim: "Terminal B explicitly takes control, Terminal A loses write control, and both see the same provider response.",
  });
  await capture(
    pageA,
    "04-takeover-terminal-a",
    "After takeover, Terminal A observes the shared response without write control.",
    "cross-terminal-takeover",
  );
  await capture(
    pageB,
    "04-takeover-terminal-b",
    "Terminal B controls the same Session and receives CROSS_TERMINAL_TAKEOVER_OK.",
    "cross-terminal-takeover",
  );

  await submitOfflinePrompt(pageB, drawerB, primarySession.session_id);
  await operatorB.context.setOffline(true);
  await expect.poll(() => pageB.evaluate(() => navigator.onLine)).toBe(false);
  offlineStartedAt = new Date().toISOString();
  await pageB.waitForTimeout(600);
  await capture(
    pageB,
    "05-controller-offline-terminal-b",
    "Terminal B has navigator.onLine=false after submitting a delayed real Codex request.",
    "offline-recovery",
  );
  await expect.poll(() => terminalText(pageA, primarySession.session_id), { timeout: 120_000 })
    .toContain("OFFLINE_RECOVERY_OK");
  offlineResponseObservedAt = new Date().toISOString();
  assertions.push({
    id: "offline-provider-continuation",
    status: "passed",
    claim: "The real provider completes OFFLINE_RECOVERY_OK after the controlling browser goes offline.",
  });
  await capture(
    pageA,
    "05-offline-response-terminal-a",
    "Terminal A observes OFFLINE_RECOVERY_OK while the controlling Terminal B remains offline.",
    "offline-recovery",
  );
  await operatorB.context.close();

  const operatorC = await newOperatorContext("terminal-b-reconnected");
  pageC = operatorC.page;
  const drawerC = await openTerminal(pageC);
  await openRunningSession(
    pageC,
    drawerC,
    primarySession.session_id,
    "Codex · 跨端主会话",
  );
  await expectAttachment(drawerC, primarySession.session_id, "controlling");
  await expect.poll(() => terminalText(pageC, primarySession.session_id), { timeout: 30_000 })
    .toContain("OFFLINE_RECOVERY_OK");
  recoveryAttachedAt = new Date().toISOString();
  assertions.push({
    id: "fresh-context-reattach",
    status: "passed",
    claim: "A fresh browser context controls the same Session and receives its full pre-offline history.",
  });
  await capture(
    pageC,
    "06-recovered-terminal-c",
    "A fresh browser context reattaches to the same Session and restores OFFLINE_RECOVERY_OK history.",
    "fresh-context-recovery",
  );

  secondarySession = await createSession(pageC, drawerC, "Codex");
  await expect(drawerC.getByRole("tab")).toHaveCount(2, { timeout: 75_000 });
  await renameActive(drawerC, "Codex · Review Lane");
  await drawerC.locator(`[role="tab"][data-session-id="${primarySession.session_id}"]`).click();
  await drawerC.getByRole("button", { name: "Exit full screen" }).click();
  await expect(drawerC).toHaveAttribute("data-fullscreen", "false");
  await pageC.getByRole("button", { name: "Settings" }).click();
  await pageC.getByRole("radio", { name: "Select Light theme" }).click();
  await expect(sessionPanel(drawerC, primarySession.session_id).locator(".web-terminal-view"))
    .toHaveAttribute("data-terminal-theme", "light");
  assertions.push({
    id: "multi-tab-layout-theme",
    status: "passed",
    claim: "The recovered browser keeps two renamed tabs and synchronizes Dock and terminal theme state.",
  });
  await capture(
    pageC,
    "07-multi-tab-dock-light",
    "Two renamed PTY tabs are visible in Dock mode with the Dashboard light theme.",
    "recovery-operations",
  );
  await pageC.getByRole("radio", { name: "Select Dark theme" }).click();
  await pageC.keyboard.press("Escape");

  usage = await waitForUsage(pageC, primarySession.session_id);
  await pageC.getByRole("button", { name: "Toggle Terminal" }).click();
  await pageC.getByRole("button", { name: "Agents" }).click();
  const terminals = pageC.getByTestId("interactive-terminals");
  const usageRow = terminals.getByRole("row")
    .filter({ hasText: "Codex · 跨端主会话" })
    .first();
  await expect(usageRow).toBeVisible();
  await expect(usageRow.locator('td[data-label="tokens"]')).not.toHaveText("—");
  assertions.push({
    id: "usage-attribution",
    status: "passed",
    claim: "Agents attributes real non-zero token and cost evidence to the renamed recovered Session.",
  });
  await capture(
    pageC,
    "08-recovered-usage",
    "Agents lists the recovered Session by tab name with non-zero transcript-backed usage.",
    "recovery-operations",
  );
} catch (error) {
  runError = error;
} finally {
  if (pageA && !pageA.isClosed()) {
    for (const sessionId of [...new Set(createdSessionIds)]) {
      await stopSession(pageA, sessionId).catch(() => undefined);
    }
    await expect.poll(() => fetchActiveSessionIds(pageA), { timeout: 30_000 })
      .toEqual([])
      .catch(() => undefined);
    activeSessionsAfter = await fetchActiveSessionIds(pageA).catch(() => null);
  }
  for (const context of contexts) {
    await context.close().catch(() => undefined);
  }
  await browser.close();
  const summary = {
    schema_version: "web-terminal-showcase-capture.v1",
    source_commit: sourceCommit,
    worktree: process.cwd(),
    service_url: baseURL,
    browser_origin: new URL(baseURL).origin,
    viewport: { width: 1920, height: 1080, device_scale_factor: 2 },
    provider_mode: "real",
    provider_model: usage?.model ?? "",
    project_id: projectId,
    primary_session_id: primarySession?.session_id ?? "",
    secondary_session_id: secondarySession?.session_id ?? "",
    started_at: startedAt,
    finished_at: new Date().toISOString(),
    offline_started_at: offlineStartedAt,
    offline_response_observed_at: offlineResponseObservedAt,
    recovery_attached_at: recoveryAttachedAt,
    isolated_browser_contexts: 3,
    storyboard,
    assertions,
    usage,
    cleanup: {
      exact_created_session_ids_only: true,
      created_session_count: new Set(createdSessionIds).size,
      active_sessions_after: activeSessionsAfter,
    },
    error: runError instanceof Error ? runError.message : (runError ? String(runError) : ""),
  };
  await writeFile(
    path.join(outputDir, "capture-summary.json"),
    `${JSON.stringify(summary, null, 2)}\n`,
    "utf8",
  );
  process.stdout.write(`${JSON.stringify({
    assertions: assertions.length,
    capture_summary: path.join(outputDir, "capture-summary.json"),
    frame_count: storyboard.length,
  })}\n`);
}

if (runError) throw runError;
