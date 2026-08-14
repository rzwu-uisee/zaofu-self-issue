import { expect, test, type Page } from "@playwright/test";

const projectId = process.env.ZF_WEB_PROJECT_ID ?? "default";

async function openLongRunTruth(page: Page, width: number, height: number) {
  await page.setViewportSize({ width, height });
  await page.goto(
    `/?project=${encodeURIComponent(projectId)}`
      + "&page=observability&obs_tab=events",
  );
  const band = page.getByTestId("long-run-truth");
  await expect(page.getByTestId("observability-page")).toBeVisible();
  await expect(band).toHaveAttribute("data-state", "ready");
  return band;
}

test("current run truth separates event volume from current operations", async ({ page }) => {
  const response = await page.request.get(
    `/api/projects/${encodeURIComponent(projectId)}/long-run-truth`,
  );
  expect(response.ok()).toBe(true);
  const projection = await response.json();
  expect(projection.current).toMatchObject({
    run_id: "run-airport-browser",
    task_map_generation: "map-current",
    candidate_ref: "candidate/airport",
  });
  expect(projection.counts.raw_events).toBeGreaterThan(projection.counts.unique_operations);
  expect(projection.counts.unique_operations).toBe(2);

  const band = await openLongRunTruth(page, 1440, 920);
  await expect(band).toContainText("run-airport-browser");
  await expect(band).toContainText("map-current");
  await expect(band).toContainText("candidate/airport");
  await expect(band.locator(".long-run-truth-facts > span", { hasText: "Events" }).locator("strong")).toHaveText(
    projection.counts.raw_events.toLocaleString("en-US"),
  );
  await expect(band.locator(".long-run-truth-facts > span", { hasText: "Operations" }).locator("strong")).toHaveText("2");
  await expect(band).toContainText("owner: approval required");
  await expect(band).toContainText("ck-airport x3");
  await expect(band.locator(".long-run-truth-milestones .is-proven")).toHaveCount(4);
});

test("current run truth remains bounded on a mobile viewport", async ({ page }) => {
  const band = await openLongRunTruth(page, 390, 844);
  const geometry = await band.evaluate((element) => ({
    bandLeft: element.getBoundingClientRect().left,
    bandRight: element.getBoundingClientRect().right,
    documentClientWidth: document.documentElement.clientWidth,
    documentScrollWidth: document.documentElement.scrollWidth,
    childRights: Array.from(element.children).map((child) => (
      child.getBoundingClientRect().right
    )),
  }));

  expect(geometry.bandLeft).toBeGreaterThanOrEqual(-1);
  expect(geometry.bandRight).toBeLessThanOrEqual(geometry.documentClientWidth + 1);
  expect(geometry.documentScrollWidth).toBeLessThanOrEqual(geometry.documentClientWidth + 1);
  for (const right of geometry.childRights) {
    expect(right).toBeLessThanOrEqual(geometry.documentClientWidth + 1);
  }
  await expect(band.locator(".long-run-truth-milestones")).toBeVisible();
});
