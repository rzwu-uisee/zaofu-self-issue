import { expect, test } from "@playwright/test";


const projectId = process.env.ZF_NEEDS_INPUT_PROJECT_ID ?? "";
const actionToken = process.env.ZF_WEB_ACTION_TOKEN_FOR_TEST ?? "";

test.describe.configure({ mode: "serial", timeout: 90_000 });

async function authorize(page: import("@playwright/test").Page) {
  await page.addInitScript(({ token }) => {
    window.localStorage.setItem("zf.webActionToken", token);
  }, { token: actionToken });
}

test("Channel decisions and Inbox links close through their source surfaces", async ({
  page,
  request,
}) => {
  expect(projectId).not.toBe("");
  expect(actionToken).not.toBe("");
  await authorize(page);

  await page.goto(
    `/?project=${encodeURIComponent(projectId)}&page=channels`
      + "&channel=ch-running-decisions",
  );
  const running = page.getByTestId("channel-discussion-attention");
  await expect(running).toContainText("1 agent responding");
  await expect(running).toContainText("1 decision pending");
  await expect(running).toContainText("blocks synthesis");
  await expect(page.getByTestId("channel-owner-question-shelf")).toHaveCount(0);
  await running.getByRole("button", { name: "Review 1" }).click();
  await expect(page.getByTestId("channel-owner-question-shelf")).toBeVisible();
  await page.getByRole("button", { name: "Close question" }).click();

  await page.goto(
    `/?project=${encodeURIComponent(projectId)}&page=channels`
      + "&channel=ch-waiting-decisions",
  );
  const waiting = page.getByTestId("channel-discussion-attention");
  const shelf = page.getByTestId("channel-owner-question-shelf");
  await expect(waiting).toContainText("Waiting for you");
  await expect(waiting).toContainText("1 decision blocks synthesis");
  await expect(shelf).toBeVisible();

  await page.setViewportSize({ width: 390, height: 844 });
  await expect(shelf).toBeVisible();
  const shelfBounds = await shelf.boundingBox();
  expect(shelfBounds).not.toBeNull();
  expect((shelfBounds?.x ?? 0) + (shelfBounds?.width ?? 0)).toBeLessThanOrEqual(390);
  await shelf.getByLabel("Developers").check();
  await shelf.getByTestId("ask-user-submit").click();
  await expect(shelf).toHaveCount(0);
  await expect(waiting).not.toContainText("Waiting for you");

  await expect.poll(async () => {
    const response = await request.get(
      `/api/projects/${encodeURIComponent(projectId)}/operator/inbox`,
    );
    const inbox = await response.json();
    return Number(inbox.summary?.decisions_pending ?? -1);
  }).toBe(2);

  await page.setViewportSize({ width: 1280, height: 800 });
  await page.goto(`/?project=${encodeURIComponent(projectId)}&page=inbox`);
  await expect(page.getByText("2 decisions", { exact: true })).toBeVisible();

  const channelDecision = page.locator(".operator-inbox-row").filter({
    hasText: "Which launch scope should the team optimize for?",
  });
  await channelDecision.click();
  await channelDecision.getByRole("link", { name: "Open channel" }).click();
  await expect(page).toHaveURL(/page=channels/);
  await expect(page).toHaveURL(/channel=ch-running-decisions/);
  await expect(page.getByTestId("channel-discussion-attention")).toContainText(
    "1 agent responding",
  );

  await page.goto(`/?project=${encodeURIComponent(projectId)}&page=inbox`);
  const kanbanDecision = page.locator(".operator-inbox-row").filter({
    hasText: "Which workflow should execute this task?",
  });
  await kanbanDecision.click();
  await kanbanDecision.getByRole("link", { name: "Open Kanban Agent" }).click();
  await expect(page).toHaveURL(/page=project/);
  await expect(page.getByRole("dialog", { name: "Kanban Agent" })).toBeVisible();
});
