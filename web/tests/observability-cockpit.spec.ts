import { expect, test } from "@playwright/test";

const projectId = process.env.ZF_WEB_PROJECT_ID ?? "zaofu-915bc1fe";

async function openObservability(page: import("@playwright/test").Page, theme: "light" | "dark", width: number, height: number) {
  await page.setViewportSize({ width, height });
  await page.addInitScript((mode) => {
    window.localStorage.setItem("zf.themeMode", mode);
  }, theme);
  await page.goto(`/?project=${encodeURIComponent(projectId)}&page=observability&obs_tab=events`);
  await expect(page.locator("html")).toHaveAttribute("data-theme", theme);
  await expect(page.locator('[data-testid="observability-page"]')).toBeVisible();
  await expect(page.getByRole("heading", { name: "Observability" })).toBeVisible();
}

test("observability cockpit supports desktop light and dark", async ({ page }) => {
  await openObservability(page, "light", 1440, 920);
  await expect(page.locator(".observability-replay-bar")).toBeVisible();
  await expect(page.getByRole("button", { name: /Play|Pause/ })).toBeVisible();
  await expect(page.getByRole("button", { name: "Runtime Logs", exact: true })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Raw", exact: true })).toHaveCount(0);
  await page.getByTestId("observability-page").getByRole("button", {
    name: /^Traces/,
  }).click();
  await expect(page.getByTestId("traces-page")).toBeVisible();
  await expect(page.getByRole("heading", { name: "Traces", exact: true })).toBeVisible();

  await openObservability(page, "dark", 1440, 920);
  await expect(page.locator(".observability-replay-bar")).toBeVisible();
});

test("observability cockpit remains usable on mobile", async ({ page }) => {
  await openObservability(page, "light", 390, 844);
  await expect(page.locator(".observability-workbench.events-workbench")).toBeVisible();
  await expect(page.locator(".observability-replay-bar")).toBeVisible();
  await expect(page.getByRole("heading", { name: "Event Views" })).toBeVisible();
  await page.getByTestId("observability-page").getByRole("button", {
    name: /^Traces/,
  }).click();
  await expect(page.getByTestId("traces-page")).toBeVisible();
  await expect(page.locator(".trace-index-panel")).toBeVisible();
});

test("trace route uses the scoped Traces product surface", async ({ page }) => {
  await page.goto(`/?project=${encodeURIComponent(projectId)}&page=traces`);
  await expect(page.getByTestId("traces-page")).toBeVisible();
  await expect(page.getByRole("heading", { name: "Traces", exact: true })).toBeVisible();
  await expect(page.locator(".trace-index-panel")).toBeVisible();
  await expect(page.locator(".projection-metric-grid")).toHaveCount(0);
  await expect(page.locator(".runtime-health-strip")).toHaveCount(0);
  await expect(page.getByTestId("long-run-truth")).toHaveCount(0);
  await expect(page.locator(".event-inspector")).toHaveCount(0);
});

test("legacy entity routes open their canonical Observability tabs", async ({ page }) => {
  for (const route of ["runs", "fanouts", "candidates"] as const) {
    await page.goto(`/?project=${encodeURIComponent(projectId)}&page=${route}`);
    await expect(page.getByTestId("observability-page")).toBeVisible();
    await expect(page.getByRole("button", { name: new RegExp(`^${route}`, "i") })).toHaveClass(/active/);
  }
});

test("core cockpit pages render on desktop and mobile", async ({ page }) => {
  const routes = [
    { pageId: "project", heading: "Project" },
    { pageId: "board", heading: "Tasks" },
    { pageId: "delivery", heading: "Delivery" },
    { pageId: "agents", heading: "Agents" },
    { pageId: "runtime", heading: "Observability" },
    { pageId: "observability", heading: "Traces" },
    { pageId: "channels", selector: ".channel-shell" },
    { pageId: "automations", heading: "Automations" },
  ] as const;
  for (const [width, height] of [[1366, 900], [390, 844]] as const) {
    await page.setViewportSize({ width, height });
    await page.addInitScript(() => {
      window.localStorage.setItem("zf.themeMode", "light");
    });
    for (const route of routes) {
      const { pageId } = route;
      await page.goto(`/?project=${encodeURIComponent(projectId)}&page=${pageId}`);
      if ("heading" in route) {
        await expect(page.getByRole("heading", { name: route.heading, exact: true })).toBeVisible();
      } else {
        await expect(page.locator(route.selector)).toBeVisible();
      }
      await expect(page.locator("main.workspace")).toBeVisible();
    }
  }
});
