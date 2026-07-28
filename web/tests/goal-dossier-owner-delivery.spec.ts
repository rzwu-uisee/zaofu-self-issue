import { expect, test, type Page } from "@playwright/test";

const projectId = process.env.ZF_WEB_PROJECT_ID ?? "default";
const runId = process.env.ZF_GOAL_DOSSIER_RUN_ID ?? "run-owner-delivery";

async function openRunDossier(page: Page, width: number, height: number) {
  await page.setViewportSize({ width, height });
  await page.goto(
    `/?project=${encodeURIComponent(projectId)}`
      + `&page=observability&obs_tab=runs&obs_run_id=${encodeURIComponent(runId)}`,
  );
  await expect(page.getByTestId("observability-page")).toBeVisible();
  await expect(page.getByTestId("run-goal-dossier")).toBeVisible();
}

test("terminal run deep link renders the owner Goal Dossier", async ({ page }) => {
  await openRunDossier(page, 1440, 920);
  const dossier = page.getByTestId("run-goal-dossier");

  await expect(dossier.getByRole("heading", { name: "Goal Dossier" })).toBeVisible();
  await expect(dossier.getByText("ship owner-readable delivery")).toBeVisible();
  await expect(dossier.getByText("completed", { exact: true })).toBeVisible();

  for (const section of ["Tasks", "Claims", "Evidence", "Gaps", "Closure"]) {
    await dossier.getByRole("button", { name: section, exact: true }).click();
    await expect(
      dossier.getByRole("button", { name: section, exact: true }),
    ).toHaveClass(/active/);
  }
});

test("terminal Goal Dossier remains usable on mobile", async ({ page }) => {
  await openRunDossier(page, 390, 844);
  const dossier = page.getByTestId("run-goal-dossier");

  await expect(dossier.getByRole("button", { name: "Tasks", exact: true })).toBeVisible();
  await dossier.getByRole("button", { name: "Tasks", exact: true }).click();
  await expect(dossier.getByText("TASK-1", { exact: true })).toBeVisible();
  const fitsViewport = await dossier.evaluate(
    (element) => element.getBoundingClientRect().right <= window.innerWidth + 1,
  );
  expect(fitsViewport).toBe(true);
});
