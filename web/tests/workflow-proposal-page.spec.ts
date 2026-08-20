import { expect, test, type Page } from "@playwright/test";

const actionToken = process.env.ZF_WEB_ACTION_TOKEN_FOR_TEST ?? "";
const requestId = process.env.ZF_WORKFLOW_PROPOSAL_REQUEST_ID ?? "REQ-PW-WORKFLOW";
const seedKey = `workflow-proposal-browser-seed:${requestId}`;

test.describe.configure({ mode: "serial", timeout: 60_000 });
test.skip(!actionToken, "Workflow Proposal E2E requires an isolated action token.");

async function expectNoViewportOverflow(page: Page) {
  const metrics = await page.evaluate(() => ({
    clientWidth: document.documentElement.clientWidth,
    scrollWidth: document.documentElement.scrollWidth,
  }));
  expect(metrics.scrollWidth).toBeLessThanOrEqual(metrics.clientWidth + 1);
}

async function expectVisibleControlsInViewport(page: Page) {
  const metrics = await page.locator(".workflow-proposal-actions button:visible").evaluateAll(
    (buttons) => {
      const viewportWidth = document.documentElement.clientWidth;
      return buttons.map((button) => {
        const rect = button.getBoundingClientRect();
        return {
          left: rect.left,
          right: rect.right,
          scrollWidth: (button as HTMLElement).scrollWidth,
          width: rect.width,
          viewportWidth,
        };
      });
    },
  );
  expect(metrics.length).toBeGreaterThan(0);
  for (const metric of metrics) {
    expect(metric.left).toBeGreaterThanOrEqual(0);
    expect(metric.right).toBeLessThanOrEqual(metric.viewportWidth + 1);
    expect(metric.scrollWidth).toBeLessThanOrEqual(metric.width + 2);
  }
}

test.beforeAll(async ({ request }) => {
  const response = await request.post(
    "/api/projects/default/actions/workflow-request",
    {
      headers: {
        "x-idempotency-key": seedKey,
        "x-zf-web-token": actionToken,
      },
      data: {
        project_id: "default",
        idempotency_key: seedKey,
        actor: "playwright",
        payload: {
          request_id: requestId,
          kind: "issue",
          objective: "Fix checkout expiry and retain an auditable regression test.",
          acceptance: [
            "An active checkout session remains valid.",
            "The regression suite proves the expiry boundary.",
          ],
          constraints: ["Do not change the public session API."],
          backend: "mock",
          allow_missing_env: true,
        },
      },
    },
  );
  expect(response.status(), await response.text()).toBe(202);
});

test.beforeEach(async ({ page }) => {
  await page.addInitScript((token) => {
    window.localStorage.clear();
    window.localStorage.setItem("zf.webActionToken", token);
    window.localStorage.setItem("zf.themeMode", "light");
  }, actionToken);
});

test("reviews, approves, and follows a proposal without layout collisions", async ({ page, request }) => {
  let terminalProjection: Record<string, unknown> | null = null;
  let terminalProjectionHits = 0;
  await page.route("**/api/projects/*/workflow-requests/*", async (route) => {
    const path = new URL(route.request().url()).pathname;
    if (!path.endsWith(`/workflow-requests/${requestId}`) || !terminalProjection) {
      await route.continue();
      return;
    }
    terminalProjectionHits += 1;
    await route.fulfill({ json: terminalProjection });
  });

  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto(`/?page=workflows&project=default`);

  const proposalPage = page.getByTestId("workflow-proposal-page");
  await expect(proposalPage).toBeVisible();
  await expect(page.getByRole("heading", { name: "Workflows" })).toBeVisible();
  await expect(page.getByRole("tab", { name: /Needs decision/ })).toHaveAttribute(
    "aria-selected",
    "true",
  );
  await expect(page.getByRole("heading", {
    name: "Fix checkout expiry and retain an auditable regression test.",
  })).toBeVisible();
  await expect(page.getByTestId("workflow-proposal-graph")).toBeVisible();
  await expect(page.getByText("Execution Plan", { exact: true })).toBeVisible();
  await expect(page.getByTestId("workflow-readiness")).toContainText("Ready to run");
  const advanced = page.locator(".workflow-proposal-advanced");
  await expect(advanced).not.toHaveAttribute("open", "");
  await advanced.locator("summary").first().click();
  await expect(page.getByText("Execution Closure", { exact: true })).toBeVisible();
  await expect(page.getByText("Decision Binding", { exact: true })).toBeVisible();
  await expect(page.getByText("direct-v1", { exact: true }).first()).toBeVisible();
  const approve = page.getByTestId("workflow-approve-run");
  await expect(approve).toBeEnabled();
  await expectNoViewportOverflow(page);
  await expectVisibleControlsInViewport(page);

  const workbenchBounds = await page.locator(".workflow-proposal-workbench").evaluate(
    (workbench) => {
      const list = workbench.querySelector(".workflow-request-list")!.getBoundingClientRect();
      const detail = workbench.querySelector(".workflow-proposal-detail")!.getBoundingClientRect();
      return { detailLeft: detail.left, listRight: list.right };
    },
  );
  expect(workbenchBounds.listRight).toBeLessThanOrEqual(workbenchBounds.detailLeft + 1);

  await page.setViewportSize({ width: 390, height: 844 });
  await expectNoViewportOverflow(page);
  await approve.scrollIntoViewIfNeeded();
  await expectVisibleControlsInViewport(page);
  const mobileBounds = await page.locator(".workflow-proposal-workbench").evaluate(
    (workbench) => {
      const list = workbench.querySelector(".workflow-request-list")!.getBoundingClientRect();
      const detail = workbench.querySelector(".workflow-proposal-detail")!.getBoundingClientRect();
      return { detailTop: detail.top, listBottom: list.bottom };
    },
  );
  expect(mobileBounds.listBottom).toBeLessThanOrEqual(mobileBounds.detailTop + 1);

  const submitted = page.waitForResponse((response) => (
    response.url().endsWith("/actions/workflow-submit")
    && response.request().method() === "POST"
  ));
  await approve.click();
  expect((await submitted).status()).toBe(202);
  await expect(page.getByTestId("workflow-proposal-feedback")).toContainText("accepted");
  await expect(page.getByTestId("workflow-open-run")).toBeVisible();

  const detailResponse = await request.get(
    `/api/projects/default/workflow-requests/${requestId}`,
  );
  expect(detailResponse.status()).toBe(200);
  const body = await detailResponse.json() as Record<string, unknown>;
  const lifecycle = body.lifecycle as Record<string, unknown>;
  const links = body.links as Record<string, unknown>;
  terminalProjection = {
    ...body,
    lifecycle: {
      ...lifecycle,
      run_id: requestId,
      run_started: true,
      submitted: true,
      terminal: "run.goal.completed",
    },
    links: {
      ...links,
      completion_receipt_ref: "artifacts/goal-completion-receipt.v1.json",
      goal_dossier_ref: "artifacts/goal-dossier.v1.json",
    },
  };
  await page.reload({ waitUntil: "domcontentloaded" });
  await expect.poll(() => terminalProjectionHits).toBeGreaterThan(0);
  await expect(page.getByTestId("workflow-readiness")).toContainText("Run completed");
  await expectNoViewportOverflow(page);

  await page.getByTestId("workflow-open-run").click();
  await expect(page).toHaveURL(/page=runs/);
  await expect(page).toHaveURL(new RegExp(`run_id=${encodeURIComponent(requestId)}`));
});
