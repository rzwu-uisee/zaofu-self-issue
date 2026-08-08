import { expect, test, type APIRequestContext, type Page } from "@playwright/test";

const token = process.env.ZF_WEB_ACTION_TOKEN_FOR_TEST ?? "";
const requestId = process.env.ZF_FOUR_FLOW_WORKFLOW_REQUEST_ID ?? "";
const evidenceDir = process.env.ZF_PLAYWRIGHT_EVIDENCE_DIR ?? "";
const objective = "Produce a verified minimal delivery note.";

type Snapshot = {
  project?: { project_id?: string };
};

test.describe.configure({ mode: "serial", timeout: 180_000 });

async function projectId(request: APIRequestContext): Promise<string> {
  const response = await request.get("/api/snapshot");
  expect(response.ok(), await response.text()).toBeTruthy();
  const snapshot = await response.json() as Snapshot;
  const id = String(snapshot.project?.project_id ?? "");
  expect(id).not.toBe("");
  return id;
}

async function capture(page: Page, name: string): Promise<void> {
  if (!evidenceDir) return;
  await page.screenshot({
    fullPage: true,
    path: `${evidenceDir}/${name}.png`,
  });
}

test("creates and applies a registered General Workflow without starting it", async ({
  page,
  request,
}) => {
  expect(token).not.toBe("");
  expect(requestId).not.toBe("");
  const id = await projectId(request);
  const idempotencyKey = `four-flow-workflow-install:${requestId}`;
  const queued = await request.post(
    `/api/projects/${encodeURIComponent(id)}/actions/workflow-request`,
    {
      headers: {
        "x-idempotency-key": idempotencyKey,
        "x-zf-web-token": token,
      },
      data: {
        project_id: id,
        idempotency_key: idempotencyKey,
        actor: "playwright",
        payload: {
          request_id: requestId,
          kind: "workflow",
          objective,
          acceptance: [
            "The registered workflow exposes a verified report artifact.",
          ],
          constraints: [
            "Use only registered generic operations and read-only roles.",
          ],
          backend: "mock",
          synthesis_backend: "claude-headless",
          allow_missing_env: true,
        },
      },
    },
  );
  expect(queued.status(), await queued.text()).toBe(202);

  await page.addInitScript((actionToken) => {
    window.localStorage.clear();
    window.localStorage.setItem("zf.webActionToken", actionToken);
    window.localStorage.setItem("zf.themeMode", "light");
  }, token);
  await page.goto(
    `/?page=workflows&project=${encodeURIComponent(id)}`,
  );
  await expect(page.getByTestId("workflow-proposal-page")).toBeVisible({
    timeout: 30_000,
  });
  await expect(page.getByTitle(requestId)).toBeVisible({
    timeout: 120_000,
  });
  await expect(page.getByRole("heading", { name: objective })).toBeVisible({
    timeout: 120_000,
  });
  await expect(page.getByText("scope", {
    exact: true,
  }).first()).toBeVisible({ timeout: 120_000 });

  const apply = page.getByTestId("workflow-apply-config");
  await expect(apply).toBeEnabled({ timeout: 120_000 });
  await capture(page, "01-general-workflow-proposal");
  const responsePromise = page.waitForResponse((response) => (
    response.url().endsWith("/actions/workflow-config-apply")
    && response.request().method() === "POST"
  ));
  await apply.click();
  const applied = await responsePromise;
  expect(applied.status(), await applied.text()).toBe(200);
  await expect(page.getByTestId("workflow-proposal-feedback")).toContainText(
    "Config applied",
  );
  await expect(page.getByTestId("workflow-approve-run")).toBeVisible();
  await capture(page, "02-general-workflow-config-applied");
});
