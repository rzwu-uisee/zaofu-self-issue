import { expect, test, type Locator, type Page } from "@playwright/test";

interface TerminalUsageProjection {
  status: string;
  total_tokens: number | null;
  cost_usd: number | null;
  cost_kind: string;
}

interface TerminalSessionProjection {
  session_id: string;
  title: string;
  provider: string;
  state: string;
  usage?: TerminalUsageProjection;
}

const enabled = process.env.ZF_WEB_REAL_TERMINAL_USAGE_E2E === "1";
const requestedProviders = new Set(
  (process.env.ZF_WEB_REAL_TERMINAL_USAGE_PROVIDERS ?? "claude-code,codex")
    .split(",")
    .map((value) => value.trim())
    .filter(Boolean),
);
test.skip(!enabled, "set ZF_WEB_REAL_TERMINAL_USAGE_E2E=1 for the explicit real-provider tier");

async function sessions(page: Page, projectId: string): Promise<TerminalSessionProjection[]> {
  return page.evaluate(async (id) => {
    const response = await fetch(
      `/api/projects/${encodeURIComponent(id || "default")}/terminal-sessions`,
      { cache: "no-store" },
    );
    if (!response.ok) throw new Error(`terminal sessions failed: ${response.status}`);
    const body = await response.json() as { sessions: TerminalSessionProjection[] };
    return body.sessions;
  }, projectId);
}

async function createTerminal(
  page: Page,
  drawer: Locator,
  providerLabel: "Claude Code" | "Codex",
): Promise<TerminalSessionProjection> {
  const responsePromise = page.waitForResponse((response) => (
    response.request().method() === "POST"
    && /\/terminal-sessions$/.test(new URL(response.url()).pathname)
  ));
  await drawer.getByRole("button", { name: "New terminal" }).click();
  await drawer.getByRole("menuitem", { name: `New ${providerLabel} terminal` }).click();
  const response = await responsePromise;
  const body = await response.json() as {
    reason?: string;
    session: TerminalSessionProjection;
    status?: string;
  };
  if (!response.ok()) {
    throw new Error(
      `${providerLabel} create failed (${response.status()}): ${body.status ?? "unknown"} · ${body.reason ?? "no reason"}`,
    );
  }
  return body.session;
}

async function renameActive(drawer: Locator, title: string): Promise<void> {
  await drawer.getByRole("button", { name: "Terminal actions" }).click();
  await drawer.getByRole("menuitem", { name: "Rename terminal" }).click();
  const input = drawer.getByRole("textbox", { name: /^Rename / });
  await input.fill(title);
  await input.press("Enter");
  await expect(drawer.getByRole("tab", { name: new RegExp(title) })).toBeVisible();
}

async function runPrompt(
  drawer: Locator,
  sessionId: string,
  prompt: string,
  expected: RegExp,
): Promise<void> {
  const tab = drawer.locator(`[role="tab"][data-session-id="${sessionId}"]`);
  await tab.click();
  const panel = drawer.locator(
    `.web-terminal-session-panel[data-session-id="${sessionId}"]`,
  );
  await expect(panel.locator(".web-terminal-view"))
    .toHaveAttribute("data-terminal-status", "controlling", { timeout: 45_000 });
  const screen = panel.locator(".xterm-rows");
  const input = panel.locator(".xterm-helper-textarea");
  await input.focus();
  const startupScreen = await screen.textContent() ?? "";
  if (/trust/i.test(startupScreen)) {
    await input.press("Enter");
    await expect.poll(async () => await screen.textContent() ?? "")
      .not.toMatch(/trust/i);
  }
  await drawer.page().waitForTimeout(750);
  await input.pressSequentially(prompt, { delay: 2 });
  await input.press("Enter");
  const firstAttempt = await expect(screen)
    .toContainText(expected, { timeout: 45_000 })
    .then(() => true)
    .catch(() => false);
  if (!firstAttempt) {
    await input.focus();
    await input.pressSequentially(prompt, { delay: 2 });
    await input.press("Enter");
    await expect(screen).toContainText(expected, { timeout: 60_000 });
  }
}

async function stableObservedUsage(
  page: Page,
  projectId: string,
  sessionId: string,
): Promise<TerminalUsageProjection> {
  let previous = "";
  for (let attempt = 0; attempt < 20; attempt += 1) {
    const session = (await sessions(page, projectId))
      .find((item) => item.session_id === sessionId);
    const usage = session?.usage;
    const encoded = usage ? JSON.stringify(usage) : "";
    if (
      usage?.status === "observed"
      && (usage.total_tokens ?? 0) > 0
      && usage.cost_usd !== null
      && encoded === previous
    ) {
      return usage;
    }
    previous = encoded;
    await page.waitForTimeout(1_000);
  }
  throw new Error(`usage did not settle for ${sessionId}: ${previous}`);
}

test("selected real Provider tabs keep usage through rename and reload", async ({ page }) => {
  test.setTimeout(240_000);
  const actionToken = process.env.ZF_WEB_ACTION_TOKEN_FOR_TEST ?? "";
  const passcode = process.env.ZF_WEB_REAL_TERMINAL_PASSCODE;
  const projectId = process.env.ZF_WEB_TERMINAL_TARGET_PROJECT_ID ?? "";
  if (!actionToken && !passcode) {
    throw new Error("ZF_WEB_ACTION_TOKEN_FOR_TEST or ZF_WEB_REAL_TERMINAL_PASSCODE is required");
  }

  const createdIds: string[] = [];
  const suffix = Date.now().toString(36);
  const claudeTitle = `ZF Claude Usage ${suffix}`;
  const codexTitle = `ZF Codex Usage ${suffix}`;
  let claude: TerminalSessionProjection | null = null;
  let codex: TerminalSessionProjection | null = null;
  let claudeUsage: TerminalUsageProjection | null = null;
  let codexUsage: TerminalUsageProjection | null = null;
  if (!requestedProviders.has("claude-code") && !requestedProviders.has("codex")) {
    throw new Error("select at least one of claude-code,codex");
  }
  await page.addInitScript(({ token, targetProjectId }) => {
    if (token) window.localStorage.setItem("zf.webActionToken", token);
    if (targetProjectId) window.localStorage.setItem("zf.activeProjectId", targetProjectId);
  }, { token: actionToken, targetProjectId: projectId });

  try {
    await page.goto(projectId
      ? `/?page=board&project=${encodeURIComponent(projectId)}`
      : "/?page=board");
    const wizard = page.getByTestId("welcome-wizard");
    if (await wizard.isVisible().catch(() => false)) {
      await page.getByTestId("welcome-skip").click();
    }
    if (!actionToken && passcode) {
      const notice = page.locator(".board-action-notice");
      await notice.getByLabel("Web passcode").fill(passcode);
      await notice.getByRole("button", { name: "Unlock" }).click();
      await expect(notice).toBeHidden();
    }

    await page.getByRole("button", { name: "Toggle Terminal" }).click();
    const drawer = page.getByRole("complementary", { name: "Coding Agent Terminal" });
    if (requestedProviders.has("claude-code")) {
      claude = await createTerminal(page, drawer, "Claude Code");
      createdIds.push(claude.session_id);
      await renameActive(drawer, claudeTitle);
      await runPrompt(
        drawer,
        claude.session_id,
        "Compute two plus two. Then append an identifier formed by joining ZF, CLAUDE, USAGE, and OK with underscores. Reply with only the result.",
        /4[\s_]+ZF_CLAUDE_USAGE_OK/,
      );
    }

    if (requestedProviders.has("codex")) {
      codex = await createTerminal(page, drawer, "Codex");
      createdIds.push(codex.session_id);
      await renameActive(drawer, codexTitle);
      await runPrompt(
        drawer,
        codex.session_id,
        "Compute three plus three. Then append an identifier formed by joining ZF, CODEX, USAGE, and OK with underscores. Reply with only the result.",
        /6[\s_]+ZF_CODEX_USAGE_OK/,
      );
    }

    await drawer.getByRole("button", { name: "Close terminal dock" }).click();
    if (claude) {
      claudeUsage = await stableObservedUsage(page, projectId, claude.session_id);
      expect(claudeUsage.cost_kind).toMatch(/estimated|partial_estimate/);
    }
    if (codex) {
      codexUsage = await stableObservedUsage(page, projectId, codex.session_id);
      expect(codexUsage.cost_kind).toMatch(/estimated|partial_estimate/);
    }

    await page.getByRole("button", { name: "Agents" }).click();
    const terminalTable = page.getByTestId("interactive-terminals");
    await expect(terminalTable.getByRole("heading", { name: "Interactive Terminals" }))
      .toBeVisible();
    if (claude) {
      const row = terminalTable.getByRole("row").filter({ hasText: claudeTitle });
      await expect(row.locator('td[data-label="provider"]')).toHaveText("claude-code");
      await expect(row.locator('td[data-label="tokens"]')).not.toHaveText("—");
      await expect(row.locator('td[data-label="cost"]')).not.toHaveText("—");
    }
    if (codex) {
      const row = terminalTable.getByRole("row").filter({ hasText: codexTitle });
      await expect(row.locator('td[data-label="provider"]')).toHaveText("codex");
      await expect(row.locator('td[data-label="tokens"]')).not.toHaveText("—");
      await expect(row.locator('td[data-label="cost"]')).not.toHaveText("—");
    }

    await page.reload();
    await page.getByRole("button", { name: "Agents" }).click();
    const afterReload = await sessions(page, projectId);
    if (claude && claudeUsage) {
      await expect(page.getByTestId("interactive-terminals").getByText(claudeTitle))
        .toBeVisible();
      expect(afterReload.find((item) => item.session_id === claude?.session_id)?.usage?.total_tokens)
        .toBe(claudeUsage.total_tokens);
    }
    if (codex && codexUsage) {
      await expect(page.getByTestId("interactive-terminals").getByText(codexTitle))
        .toBeVisible();
      expect(afterReload.find((item) => item.session_id === codex?.session_id)?.usage?.total_tokens)
        .toBe(codexUsage.total_tokens);
    }
  } finally {
    for (const sessionId of createdIds) {
      await page.evaluate(async ({ id, targetProjectId }) => {
        const token = window.localStorage.getItem("zf.webActionToken") ?? "";
        await fetch(
          `/api/projects/${encodeURIComponent(targetProjectId || "default")}/terminal-sessions/${encodeURIComponent(id)}/stop`,
          {
            method: "POST",
            headers: token
              ? { "X-ZF-Web-Token": token, Authorization: `Bearer ${token}` }
              : {},
          },
        );
      }, { id: sessionId, targetProjectId: projectId }).catch(() => undefined);
    }
  }
});
