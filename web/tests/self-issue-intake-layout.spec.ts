import { expect, test } from "@playwright/test";
import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const css = [
  "../src/styles/00-tokens-base.css",
  "../src/styles/07-agent.css",
].map((path) => readFileSync(resolve(here, path), "utf8")).join("\n");

test("Self-Issue Intake stays in the board workspace and outside the docked agent", async ({ page }) => {
  await page.setViewportSize({ width: 1600, height: 900 });
  await page.setContent(`
    <html><head><style>${css}</style><style>
      body { margin: 0; background: var(--bg); color: var(--text); }
      .workspace { height: 900px; box-sizing: border-box; }
      .project-rail { min-width: var(--workspace-rail-width); }
      .agent-page-shell { pointer-events: auto; }
      .orchestrator-panel { width: 100%; height: 100%; background: var(--panel); }
    </style></head><body>
      <main class="workspace">
        <aside class="project-rail">Workspace</aside>
        <section class="board-panel agent-docked" id="self-issue-workspace-host">
          <div>Kanban board</div>
          <div class="self-issue-intake-workspace">
          <section class="self-issue-intake" aria-label="Self-Issue questions">
            <header class="self-issue-intake-header">
              <div><strong>Report a ZaoFu bug</strong><small>Answers are saved locally.</small></div>
              <div class="self-issue-intake-progress">2/8</div>
            </header>
            <div class="self-issue-intake-question">
              <label for="bug">Describe the bug<span class="self-issue-required">*</span></label>
              <textarea id="bug" placeholder="A clear and concise description of what the bug is."></textarea>
            </div>
            <footer class="self-issue-intake-actions"><button>Back</button><button>Cancel</button><span>Saved locally</span><button>Next</button></footer>
          </section>
          </div>
        </section>
      </main>
      <section class="agent-page-shell docked">
        <div class="orchestrator-panel">
          <div class="headless-composer">
          <textarea class="headless-input" aria-label="Message"></textarea>
          </div>
        </div>
      </section>
    </body></html>
  `);

  const intake = page.getByRole("region", { name: "Self-Issue questions" });
  const answer = page.locator("#bug");
  const agent = page.locator(".agent-page-shell");
  await expect(intake).toBeVisible();
  await expect(answer).toBeVisible();
  await expect(agent).toBeVisible();
  await expect(page.locator(".headless-composer .self-issue-intake")).toHaveCount(0);
  await expect(page.locator(".self-issue-intake-progress")).toHaveText("2/8");
  await expect(answer).toHaveAttribute(
    "placeholder", "A clear and concise description of what the bug is.",
  );
  const border = await answer.evaluate((element) => getComputedStyle(element).borderTopColor);
  expect(border).not.toBe("rgba(0, 0, 0, 0)");
  await answer.fill("The Draft disappeared after refresh.");
  await expect(answer).toHaveValue("The Draft disappeared after refresh.");
  const composer = page.getByRole("textbox", { name: "Message" });
  await composer.fill("I can continue using Kanban Agent while Intake is open.");
  await expect(composer).toHaveValue("I can continue using Kanban Agent while Intake is open.");
  const intakeBox = await intake.boundingBox();
  const agentBox = await agent.boundingBox();
  expect(intakeBox).not.toBeNull();
  expect(agentBox).not.toBeNull();
  expect(intakeBox!.x + intakeBox!.width).toBeLessThanOrEqual(agentBox!.x);
});

test("Draft uses two editing columns while the GitLab preview stays full width", async ({ page }) => {
  await page.setViewportSize({ width: 1400, height: 900 });
  await page.setContent(`
    <style>${css}</style>
    <section class="self-issue-draft-card expanded">
      <div class="self-issue-draft-fields">
        <div class="self-issue-draft-side self-issue-user-report-side">
          <section class="self-issue-draft-column self-issue-user-report-core"><h3>User report</h3><label>Title<input class="filter-input" /></label><label>GitLab.com project<input class="filter-input" /></label></section>
          <section class="self-issue-draft-column self-issue-user-report-details"><label>Describe the bug<textarea class="filter-input"></textarea></label></section>
        </div>
        <div class="self-issue-draft-side self-issue-assessment-side">
          <section class="self-issue-draft-column self-issue-assessment-core"><h3>Agent &amp; Orchestrator assessment</h3><label>Severity<select class="filter-input"><option>P2</option></select></label></section>
          <section class="self-issue-draft-column self-issue-assessment-details"><label>Recommended next action<textarea class="filter-input"></textarea></label></section>
        </div>
      </div>
      <section class="self-issue-markdown-preview" aria-label="GitLab Issue Markdown preview"><div class="self-issue-preview-heading"><strong>Issue title</strong></div><h2>Describe the bug</h2><p>Exact publication body</p></section>
    </section>
  `);

  const userColumn = page.locator(".self-issue-user-report-core");
  const assessmentColumn = page.locator(".self-issue-assessment-core");
  const description = page.locator(".self-issue-user-report-details label").filter({
    hasText: "Describe the bug",
  });
  const target = page.getByText("GitLab.com project");
  const preview = page.getByRole("region", { name: "GitLab Issue Markdown preview" });
  const userBox = await userColumn.boundingBox();
  const assessmentBox = await assessmentColumn.boundingBox();
  const previewBox = await preview.boundingBox();
  expect(userBox).not.toBeNull();
  expect(assessmentBox).not.toBeNull();
  expect(previewBox).not.toBeNull();
  expect(userBox!.x + userBox!.width).toBeLessThanOrEqual(assessmentBox!.x);
  expect(previewBox!.width).toBeGreaterThan(userBox!.width + assessmentBox!.width - 40);
  const titleSize = await page.locator(".self-issue-preview-heading strong").evaluate(
    (element) => Number.parseFloat(getComputedStyle(element).fontSize),
  );
  expect(titleSize).toBeGreaterThanOrEqual(20);
  const targetBottom = await target.evaluate((element) => element.getBoundingClientRect().bottom);
  const descriptionTop = await description.evaluate((element) => element.getBoundingClientRect().top);
  expect(descriptionTop - targetBottom).toBeLessThan(32);
});

test("required Intake error remains attached to the current question", async ({ page }) => {
  await page.setContent(`
    <style>${css}</style>
    <section class="self-issue-intake">
      <div class="self-issue-intake-question">
        <label for="title">Add a title<span class="self-issue-required">*</span></label>
        <input id="title" class="invalid" placeholder="Enter a clear, concise title." />
        <div class="self-issue-intake-error" role="alert">This question can not be empty</div>
      </div>
    </section>
  `);
  await expect(page.getByRole("alert")).toHaveText("This question can not be empty");
  const inputBottom = await page.locator("#title").evaluate((element) => element.getBoundingClientRect().bottom);
  const errorTop = await page.getByRole("alert").evaluate((element) => element.getBoundingClientRect().top);
  expect(errorTop).toBeGreaterThanOrEqual(inputBottom);
});

test("attachment controls and rows remain above the Intake footer", async ({ page }) => {
  await page.setViewportSize({ width: 720, height: 760 });
  await page.setContent(`
    <style>${css}</style>
    <section class="self-issue-intake" aria-label="Self-Issue questions">
      <header class="self-issue-intake-header"><strong>Report a ZaoFu bug</strong><span>5/8</span></header>
      <div class="self-issue-intake-question">
        <label for="context">Screenshots, videos, and logs</label>
        <textarea id="context">Observed layout problem</textarea>
        <div class="self-issue-attachment-picker">
          <label class="self-issue-video-confirmation"><input type="checkbox" /> Public video confirmation</label>
          <input type="file" />
          <ul><li><span>collision-screenshot.png · 8.9 KB</span><button>Remove</button></li></ul>
        </div>
      </div>
      <footer class="self-issue-intake-actions"><button>Back</button><button>Cancel</button><span>Saved locally</span><button>Next</button></footer>
    </section>
  `);

  const attachmentBottom = await page.locator(".self-issue-attachment-picker").evaluate(
    (element) => element.getBoundingClientRect().bottom,
  );
  const footerTop = await page.locator(".self-issue-intake-actions").evaluate(
    (element) => element.getBoundingClientRect().top,
  );
  expect(footerTop).toBeGreaterThanOrEqual(attachmentBottom);
});
