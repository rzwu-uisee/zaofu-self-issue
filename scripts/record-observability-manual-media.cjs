/*
 * Run inside mcp/playwright:latest after prepare-observability-manual-demo.py.
 * It records real ZaoFu Web navigation and saves WebM plus a PNG poster for
 * each manual scene. WebP conversion is intentionally separate so the browser
 * recording stays reproducible and traceable to the real page.
 */

const { copyFile, mkdir } = require("node:fs/promises");
const { existsSync, readdirSync } = require("node:fs");
const path = require("node:path");
const { chromium } = require("/app/node_modules/playwright");

const BASE_URL = process.env.ZF_MANUAL_WEB_URL || "http://127.0.0.1:8002";
const OUTPUT_DIR = process.env.ZF_MANUAL_MEDIA_DIR || "/artifacts";
const VIEWPORT = { width: 1440, height: 900 };

function chromiumExecutablePath() {
  const configured = process.env.ZF_MANUAL_CHROMIUM_PATH;
  if (configured && existsSync(configured)) return configured;
  const root = "/ms-playwright";
  const directory = readdirSync(root, { withFileTypes: true })
    .filter((entry) => entry.isDirectory() && /^chromium-\d+$/.test(entry.name))
    .map((entry) => entry.name)
    .sort()
    .at(-1);
  if (!directory) throw new Error("no Chromium browser found under /ms-playwright");
  const executable = path.join(root, directory, "chrome-linux64", "chrome");
  if (!existsSync(executable)) throw new Error(`Chromium executable is missing: ${executable}`);
  return executable;
}

async function waitForObservability(page) {
  const configuredProjectId = process.env.ZF_MANUAL_PROJECT_ID;
  const projectId = configuredProjectId || await fetch(`${BASE_URL}/api/workspace/projects`)
    .then((response) => response.json())
    .then((payload) => String(payload.server_default_project_id || ""));
  if (!projectId) throw new Error("the Web server has no default project for manual media");
  await page.goto(`${BASE_URL}/?project=${encodeURIComponent(projectId)}&page=observability`, {
    waitUntil: "domcontentloaded",
    timeout: 30_000,
  });
  await page.getByTestId("observability-page").waitFor({ state: "visible", timeout: 20_000 });
  await page.waitForFunction(() => !/snapshot pending|slice pending/i.test(document.body.innerText), {
    timeout: 15_000,
  });
  await page.waitForTimeout(700);
}

async function openTab(page, label) {
  await page.getByRole("button", { name: new RegExp(`^${label}`) }).click();
  await page.waitForTimeout(900);
}

async function capture(browser, name, runScene) {
  const sceneDir = path.join(OUTPUT_DIR, ".recordings");
  await mkdir(sceneDir, { recursive: true });
  const context = await browser.newContext({
    viewport: VIEWPORT,
    recordVideo: { dir: sceneDir, size: VIEWPORT },
  });
  const page = await context.newPage();
  const video = page.video();
  await runScene(page);
  await page.screenshot({ path: path.join(OUTPUT_DIR, `${name}.png`), fullPage: false });
  await page.waitForTimeout(900);
  await context.close();
  await copyFile(await video.path(), path.join(OUTPUT_DIR, `${name}.webm`));
}

(async () => {
  await mkdir(OUTPUT_DIR, { recursive: true });
  const browser = await chromium.launch({
    headless: true,
    executablePath: chromiumExecutablePath(),
    args: ["--no-sandbox"],
  });
  try {
    await capture(browser, "observability-signal-routing", async (page) => {
      await waitForObservability(page);
      await openTab(page, "Events");
      await openTab(page, "Event Logs");
      await openTab(page, "Operations");
      await page.getByTestId("operations-observability").waitFor({ state: "visible", timeout: 10_000 });
      await page.waitForTimeout(1_200);
    });
    await capture(browser, "observability-runtime-log-triage", async (page) => {
      await waitForObservability(page);
      await openTab(page, "Runtime Logs");
      await page.getByLabel("Minimum runtime log level").selectOption("WARN");
      await page.waitForTimeout(1_000);
      await page.getByLabel("Minimum runtime log level").selectOption("ERROR");
      await page.waitForTimeout(1_200);
    });
    await capture(browser, "provider-telemetry-operations", async (page) => {
      await waitForObservability(page);
      await openTab(page, "Operations");
      await page.getByTestId("operations-observability").waitFor({ state: "visible", timeout: 10_000 });
      await page.locator("text=Provider Telemetry").last().scrollIntoViewIfNeeded();
      await page.waitForTimeout(1_500);
    });
  } finally {
    await browser.close();
  }
})().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
