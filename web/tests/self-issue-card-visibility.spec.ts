import { expect, test } from "@playwright/test";
import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const css = [
  "../src/styles/00-tokens-base.css",
  "../src/styles/06-agent.css",
  "../src/styles/07-agent.css",
].map((path) => readFileSync(resolve(here, path), "utf8")).join("\n");

function shell(card: string): string {
  return `
    <html>
      <head>
        <style>${css}</style>
        <style>
          body { margin: 0; background: var(--bg); color: var(--text); }
          .headless-chat { width: 100vw; height: 100vh; }
        </style>
      </head>
      <body>
        <section class="headless-chat">
          <div class="headless-thread"><p>Conversation remains independently scrollable.</p></div>
          ${card}
          <div class="headless-composer">
            <textarea class="headless-input" aria-label="Message"></textarea>
            <div class="headless-composer-footer"><button>Send</button></div>
          </div>
        </section>
      </body>
    </html>
  `;
}

test("a minimized Self-Issue Draft remains visible above the composer", async ({ page }) => {
  await page.setViewportSize({ width: 1366, height: 768 });
  await page.setContent(shell(`
    <button class="self-issue-draft-launcher" aria-label="Open Self-Issue Draft">
      <span class="self-issue-draft-launcher-main">
        <small>Self-Issue Draft</small><strong>Observed ZaoFu issue</strong>
      </span>
      <span class="self-issue-draft-launcher-status">completed</span>
      <span>↗</span>
    </button>
  `));

  const launcher = page.getByRole("button", { name: "Open Self-Issue Draft" });
  const composer = page.locator(".headless-composer");
  await expect(launcher).toBeVisible();
  await expect(composer).toBeVisible();

  const [launcherBox, composerBox] = await Promise.all([
    launcher.boundingBox(),
    composer.boundingBox(),
  ]);
  expect(launcherBox).not.toBeNull();
  expect(composerBox).not.toBeNull();
  expect(launcherBox!.y).toBeGreaterThanOrEqual(0);
  expect(launcherBox!.y + launcherBox!.height).toBeLessThanOrEqual(composerBox!.y);
  expect(composerBox!.y + composerBox!.height).toBeLessThanOrEqual(768);
});

test("an expanded Self-Issue Draft stays within a narrow viewport", async ({ page }) => {
  await page.setViewportSize({ width: 640, height: 720 });
  await page.setContent(shell(`
    <section class="headless-pending-entry self-issue-draft-card expanded"
      role="dialog" aria-label="Self-Issue Draft">
      <div class="self-issue-card-header">
        <strong>Self-Issue Draft</strong><button aria-label="Shrink Self-Issue Draft">−</button>
      </div>
      <div style="height: 1200px">Long Draft body</div>
    </section>
  `));

  const card = page.getByRole("dialog", { name: "Self-Issue Draft" });
  const header = page.locator(".self-issue-card-header");
  await expect(card).toBeVisible();
  await expect(header).toBeVisible();
  await expect(card).toHaveCSS("overflow-y", "auto");

  const box = await card.boundingBox();
  expect(box).not.toBeNull();
  expect(box!.x).toBeGreaterThanOrEqual(8);
  expect(box!.y).toBeGreaterThanOrEqual(8);
  expect(box!.x + box!.width).toBeLessThanOrEqual(632);
  expect(box!.y + box!.height).toBeLessThanOrEqual(712);
});
