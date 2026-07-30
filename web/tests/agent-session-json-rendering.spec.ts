import { expect, test } from "@playwright/test";

const expectedJson = [
  "{",
  '  "status": "healthy",',
  '  "details": {',
  '    "surface": "shared",',
  '    "items": [',
  "      1,",
  "      2",
  "    ]",
  "  }",
  "}",
].join("\n");

test("standalone JSON renders on Kanban Agent and Channel Group timelines", async ({ page }) => {
  await page.goto("/?fixture=agent-session");

  for (const testId of ["fx-kanban-compact", "fx-channel-compact"]) {
    const surface = page.getByTestId(testId);
    const block = surface.locator(".agent-code-block").filter({
      hasText: '"surface": "shared"',
    });
    await expect(block).toHaveCount(1);
    await expect(block.locator(".agent-code-header > span")).toHaveText("json");
    await expect(block.locator("pre > code")).toHaveText(expectedJson);
  }
});
