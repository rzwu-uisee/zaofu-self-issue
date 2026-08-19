import { expect, test } from "@playwright/test";


test("preparing choices hides control protocol on desktop and mobile", async ({
  page,
}) => {
  await page.goto("/?fixture=agent-session");
  const fixture = page.getByTestId("fx-kanban-preparing");
  const card = fixture.getByTestId("agent-card-preparing");

  await expect(card).toBeVisible();
  await expect(card).toContainText("Preparing choices");
  await expect(card.getByRole("button")).toHaveCount(0);
  await expect(fixture).not.toContainText("plan_request");

  await page.setViewportSize({ width: 390, height: 844 });
  await expect(card).toBeVisible();
  const bounds = await card.boundingBox();
  expect(bounds).not.toBeNull();
  expect((bounds?.x ?? 0) + (bounds?.width ?? 0)).toBeLessThanOrEqual(390);
});
