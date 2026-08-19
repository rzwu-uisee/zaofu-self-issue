import { expect, test } from "@playwright/test";

test("structured contribution keeps the main transcript concise and inspectable", async ({ page }) => {
  const browserErrors: string[] = [];
  page.on("console", (message) => {
    if (message.type() === "error") browserErrors.push(message.text());
  });
  page.on("pageerror", (error) => browserErrors.push(error.message));

  await page.goto("/?fixture=agent-session");
  const card = page.getByTestId("fx-channel-compact")
    .getByTestId("agent-card-contribution");

  await expect(card).toContainText("Key takeaways");
  await expect(card).toContainText("One authority owns state.");
  await expect(card).toContainText("Risk");
  await expect(card).toContainText("P0");
  await expect(card).toContainText("Dual writes make replay diverge.");
  await expect(card).toContainText("Decision needed");
  await expect(card).toContainText("Which vertical slice ships first?");
  await expect(card).not.toContainText("Analysis");
  await expect(card).not.toContainText("View structured details");
  await expect(card).not.toContainText("4 findings");
  await expect(card).not.toContainText("Use one controlled gateway and keep the state authority explicit.");
  await expect(card.getByText("Web remains a read-oriented projection.", { exact: true })).not.toBeVisible();
  await expect(card.getByText("Low-priority display drift.", { exact: true })).not.toBeVisible();

  const more = card.locator(".agent-contribution-more");
  await expect(more.getByText("Show 4 more", { exact: true })).toBeVisible();
  await more.locator("summary").click();
  await expect(card).toContainText("Web remains a read-oriented projection.");
  await expect(card).toContainText("Low-priority display drift.");
  await expect(card).toContainText("A UI phase label conflicts with runtime state.");
  await expect(card).toContainText("Who owns the verification decision?");

  const evidence = card.locator(".agent-contribution-evidence");
  await expect(evidence.getByText("2 sources · 2 evidence refs · 1 artifact", { exact: true })).toBeVisible();
  await expect(evidence.getByText("SPEC.md", { exact: true })).not.toBeVisible();
  await evidence.locator("summary").click();
  await expect(evidence).toContainText("SPEC.md");
  await expect(evidence).toContainText("trace:fixture");
  await expect(evidence).toContainText("channels/ch-fixture/contracts/contribution/reply.json");

  await page.setViewportSize({ width: 390, height: 844 });
  const layout = await card.evaluate((element) => {
    const rect = element.getBoundingClientRect();
    return {
      left: rect.left,
      right: rect.right,
      scrollWidth: element.scrollWidth,
      clientWidth: element.clientWidth,
      viewportWidth: window.innerWidth,
    };
  });
  expect(layout.left).toBeGreaterThanOrEqual(-1);
  expect(layout.right).toBeLessThanOrEqual(layout.viewportWidth + 1);
  expect(layout.scrollWidth).toBeLessThanOrEqual(layout.clientWidth + 1);
  expect(browserErrors).toEqual([]);
});
