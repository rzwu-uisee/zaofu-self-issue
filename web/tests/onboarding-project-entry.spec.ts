import { expect, test } from "@playwright/test";

const actionToken = process.env.ZF_WEB_ACTION_TOKEN_FOR_TEST ?? "";
const artifactDir = process.env.ZF_E2E_ARTIFACT_DIR ?? "";
const mobileViewport = process.env.ZF_E2E_VIEWPORT === "mobile";
const viewportLabel = mobileViewport ? "mobile" : "desktop";

test.skip(!actionToken, "Fresh onboarding E2E requires an action token.");
test.setTimeout(90_000);

test.beforeEach(async ({ page }) => {
  if (mobileViewport) await page.setViewportSize({ width: 390, height: 844 });
  await page.addInitScript((token) => {
    window.localStorage.clear();
    window.localStorage.setItem("zf.webActionToken", token);
    window.localStorage.setItem("zf.themeMode", "light");
  }, actionToken);
});

test("completes host onboarding without creating a project", async ({ page }) => {
  const projectMutations: string[] = [];
  page.on("request", (request) => {
    if (
      request.method() === "POST"
      && (
        request.url().endsWith("/api/workspace/projects/init")
        || request.url().endsWith("/api/workspace/projects/register")
      )
    ) {
      projectMutations.push(request.url());
    }
  });

  await page.goto("/?page=project");
  const wizard = page.getByTestId("welcome-wizard");
  await expect(wizard).toBeVisible();
  await expect(page.getByTestId("welcome-rail-backend")).toBeVisible();
  await expect(page.getByTestId("welcome-rail-preflight")).toBeVisible();
  await expect(page.getByTestId("welcome-rail-access")).toBeVisible();
  await expect(page.getByTestId("welcome-rail-ready")).toBeVisible();
  await expect(page.getByTestId("welcome-rail-project")).toHaveCount(0);
  await expect(page.getByTestId("welcome-backend-mixed")).toHaveCount(0);

  await page.getByTestId("welcome-backend-codex").click();
  const mixedToggle = page.getByTestId("welcome-mixed-enabled");
  if (await mixedToggle.isEnabled()) await mixedToggle.check();
  await page.getByTestId("welcome-continue").click();
  await expect(page.getByRole("heading", { name: "Environment" })).toBeVisible();

  const environmentContinue = page.getByTestId("welcome-continue");
  if (await environmentContinue.isEnabled()) {
    await environmentContinue.click();
  } else {
    await page.getByRole("button", { name: "跳过此步" }).click();
  }
  await expect(page.getByRole("heading", { name: "Access" })).toBeVisible();
  await expect(page.getByTestId("welcome-action-token")).toBeVisible();
  await expect(page.getByTestId("welcome-access-status")).toContainText("已授权");

  await page.getByTestId("welcome-continue").click();
  await expect(page.getByRole("heading", { name: "Ready" })).toBeVisible();
  await expect(wizard.getByText(/Team (mixed|single)/)).toBeVisible();
  await expect(wizard.getByText(/项目已建|第一个项目/)).toHaveCount(0);
  if (artifactDir) {
    await page.screenshot({
      fullPage: true,
      path: `${artifactDir}/onboarding-ready-${viewportLabel}.png`,
    });
  }

  const completed = page.waitForResponse((response) => (
    response.url().endsWith("/api/workspace/onboarding")
      && response.request().method() === "POST"
      && response.status() === 200
  ));
  await page.getByTestId("welcome-finish").click();
  await completed;
  await expect(wizard).toBeHidden();
  await expect(
    page.locator(".project-init-panel").getByRole("button", { name: "Add Project" }),
  ).toBeVisible();
  if (artifactDir) {
    await page.screenshot({
      fullPage: true,
      path: `${artifactDir}/workspace-empty-${viewportLabel}.png`,
    });
  }
  expect(projectMutations).toHaveLength(0);
});
