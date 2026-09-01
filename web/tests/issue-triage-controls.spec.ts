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

test("Issue labels fold after three into a clickable Labels popover", async ({ page }) => {
  await page.setContent(shell(`
    <span class="issue-label-row foldable" aria-label="GitHub labels">
      <button class="badge issue-label-badge issue-triage-tooltip" data-tooltip="Show only issues labelled “p1”">p1</button>
      <button class="badge issue-label-badge issue-triage-tooltip" data-tooltip="Show only issues labelled “p2”">p2</button>
      <button class="badge issue-label-badge issue-triage-tooltip" data-tooltip="Show only issues labelled “bug”">bug</button>
      <span class="issue-label-overflow-menu">
        <button aria-haspopup="menu" aria-label="Show 2 more labels" class="badge issue-label-more">+2</button>
        <span class="issue-label-overflow-popover" role="menu" aria-label="Labels">
          <strong>Labels</strong>
          <span class="issue-label-overflow-options">
            <button aria-label="Filter issues by label p1" class="badge issue-label-badge" role="menuitem">p1</button>
            <button aria-label="Filter issues by label p2" class="badge issue-label-badge" role="menuitem">p2</button>
            <button aria-label="Filter issues by label bug" class="badge issue-label-badge" role="menuitem">bug</button>
            <button aria-label="Filter issues by label performance" class="badge issue-label-badge" role="menuitem">performance</button>
            <button aria-label="Filter issues by label unknown" class="badge issue-label-badge" role="menuitem">unknown</button>
          </span>
        </span>
      </span>
    </span>
  `));
  const trigger = page.getByRole("button", { name: "Show 2 more labels" });
  const popover = page.getByRole("menu", { name: "Labels" });
  await expect(popover).toBeHidden();
  await expect(trigger).toBeVisible();
  await trigger.hover();
  await expect(popover).toBeVisible();
  await expect(popover.getByRole("menuitem", { name: "Filter issues by label p1" })).toBeVisible();
  await expect(popover.getByRole("menuitem", { name: "Filter issues by label performance" })).toBeVisible();
  await expect(popover.getByRole("menuitem", { name: "Filter issues by label unknown" })).toBeVisible();
  await popover.getByRole("menuitem", { name: "Filter issues by label performance" }).hover();
  await expect(popover).toBeVisible();
  await page.getByRole("button", { name: "p1" }).hover();
  expect(await page.getByRole("button", { name: "p1" }).evaluate((node) => getComputedStyle(node, "::after").content)).toContain("Show only issues");
});

test("toolbar hints and permanent cancellation warning are visually explicit", async ({ page }) => {
  await page.setContent(shell(`
    <header class="issue-triage-header"><div class="button-row">
      <button class="icon-button issue-triage-tooltip" data-tooltip="Fetch the latest Issue metadata and comments from GitHub">Refresh</button>
      <button class="icon-button primary issue-triage-tooltip" data-tooltip="Manually queue the selected or specified GitHub Issue for read-only Triage">Start Triage</button>
    </div></header>
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
  expect(await refresh.evaluate((node) => getComputedStyle(node, "::after").whiteSpace)).toBe("normal");
  expect(await refresh.evaluate((node) => getComputedStyle(node, "::after").right)).toBe("0px");
  await page.getByRole("button", { name: "Start Triage" }).hover();
  expect(await page.getByRole("button", { name: "Start Triage" }).evaluate((node) => getComputedStyle(node, "::after").content)).toContain("read-only Triage");
  const warning = page.locator(".issue-run-permanent-warning");
  await expect(warning).toContainText("cannot be resumed");
  await expect(warning).toContainText("does not roll back files");
  await expect(page.getByRole("button", { name: "Cancel permanently" })).toBeDisabled();
  expect(Number.parseFloat(await warning.evaluate((node) => getComputedStyle(node).borderTopWidth))).toBeGreaterThanOrEqual(2);
});
