import { expect, test } from "@playwright/test";
import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const css = [
  "../src/styles/00-tokens-base.css",
  "../src/styles/24-issue-triage.css",
].map((path) => readFileSync(resolve(here, path), "utf8")).join("\n");

function shell(body: string): string {
  return `<html><head><style>${css}</style></head><body><section class="issue-triage-shell">${body}</section></body></html>`;
}

test("Issue labels fold after three, expand on hover, and expose filter tooltips", async ({ page }) => {
  await page.setContent(shell(`
    <span class="issue-label-row foldable" aria-label="GitHub labels">
      <button class="badge issue-label-badge issue-triage-tooltip" data-tooltip="Show only issues labelled “p1”">p1</button>
      <button class="badge issue-label-badge issue-triage-tooltip" data-tooltip="Show only issues labelled “p2”">p2</button>
      <button class="badge issue-label-badge issue-triage-tooltip" data-tooltip="Show only issues labelled “bug”">bug</button>
      <button class="badge issue-label-badge issue-label-overflow issue-triage-tooltip" data-tooltip="Show only issues labelled “performance”">performance</button>
      <button class="badge issue-label-badge issue-label-overflow issue-triage-tooltip" data-tooltip="Show only issues labelled “unknown”">unknown</button>
      <button class="badge issue-label-more issue-triage-tooltip" data-tooltip="Hover or focus to show all labels">+2</button>
    </span>
  `));
  const row = page.getByLabel("GitHub labels");
  await expect(page.getByRole("button", { name: "performance" })).toBeHidden();
  await expect(page.getByRole("button", { name: "+2" })).toBeVisible();
  await row.hover();
  await expect(page.getByRole("button", { name: "performance" })).toBeVisible();
  await expect(page.getByRole("button", { name: "unknown" })).toBeVisible();
  await expect(page.getByRole("button", { name: "+2" })).toBeHidden();
  await page.getByRole("button", { name: "p1" }).hover();
  expect(await page.getByRole("button", { name: "p1" }).evaluate((node) => getComputedStyle(node, "::after").content)).toContain("Show only issues");
});

test("toolbar hints and permanent cancellation warning are visually explicit", async ({ page }) => {
  await page.setContent(shell(`
    <button class="icon-button issue-triage-tooltip" data-tooltip="Fetch the latest Issue metadata and comments from GitHub">Refresh</button>
    <button class="icon-button primary issue-triage-tooltip" data-tooltip="Manually queue the selected or specified GitHub Issue for read-only Triage">Start Triage</button>
    <div class="issue-run-permanent-warning">
      <strong>Permanent cancellation</strong>
      <p>This cannot be resumed. Cancellation does not roll back files already written to the local worktree.</p>
      <label><input type="checkbox" />I understand this Run is permanently cancelled and local files are not reverted.</label>
      <button class="icon-button danger" disabled>Cancel permanently</button>
    </div>
  `));
  const refresh = page.getByRole("button", { name: "Refresh" });
  await refresh.hover();
  expect(await refresh.evaluate((node) => getComputedStyle(node, "::after").content)).toContain("latest Issue metadata");
  await page.getByRole("button", { name: "Start Triage" }).hover();
  expect(await page.getByRole("button", { name: "Start Triage" }).evaluate((node) => getComputedStyle(node, "::after").content)).toContain("read-only Triage");
  const warning = page.locator(".issue-run-permanent-warning");
  await expect(warning).toContainText("cannot be resumed");
  await expect(warning).toContainText("does not roll back files");
  await expect(page.getByRole("button", { name: "Cancel permanently" })).toBeDisabled();
  expect(Number.parseFloat(await warning.evaluate((node) => getComputedStyle(node).borderTopWidth))).toBeGreaterThanOrEqual(2);
});
