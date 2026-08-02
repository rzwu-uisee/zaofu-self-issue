import { expect, test } from "@playwright/test";

const CASES = [
  ["fx-task-plan-options", "Full delivery Task (Recommended)"],
  ["fx-task-plan-options", "Focused Task"],
  ["fx-task-plan-options", "No Task yet"],
  ["fx-workflow-plan-options", "Delivery workflow (Recommended)"],
  ["fx-workflow-plan-options", "Research workflow"],
  ["fx-workflow-plan-options", "No workflow yet"],
] as const;

for (const [fixtureId, option] of CASES) {
  test(`Plan option can be selected and submitted: ${option}`, async ({ page }) => {
    await page.goto("/?fixture=agent-session");
    const plan = page.getByTestId(fixtureId);
    await expect(plan.getByLabel(option)).toBeVisible();
    await plan.getByLabel(option).check();
    await expect(plan.getByRole("button", { name: "Continue" })).toBeEnabled();
    await plan.getByRole("button", { name: "Continue" }).click();
    await expect(plan).toContainText("Plan summary");
    await expect(plan).toContainText(option);
  });
}
