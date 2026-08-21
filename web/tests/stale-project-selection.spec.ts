import { expect, test } from "@playwright/test";

const actionToken = process.env.ZF_WEB_ACTION_TOKEN_FOR_TEST ?? "";
const staleProjectId = "removed-project-from-another-dashboard";

test("recovers a stale persisted Project selection from the current registry", async ({ page, request }) => {
  const projectsResponse = await request.get("/api/workspace/projects");
  expect(projectsResponse.ok()).toBeTruthy();
  const projectsPage = await projectsResponse.json() as {
    active_project_id?: string;
    items?: Array<{ project_id: string }>;
    projects?: Array<{ project_id: string }>;
  };
  const projects = projectsPage.items ?? projectsPage.projects ?? [];
  const expectedProjectId = projects.some((project) => (
    project.project_id === projectsPage.active_project_id
  ))
    ? String(projectsPage.active_project_id)
    : String(projects[0]?.project_id ?? "");
  expect(expectedProjectId).not.toBe("");

  await page.addInitScript(({ staleId, token }) => {
    window.localStorage.setItem("zf.activeProjectId", staleId);
    if (token) window.localStorage.setItem("zf.webActionToken", token);
  }, { staleId: staleProjectId, token: actionToken });

  const staleRequests: string[] = [];
  page.on("request", (candidate) => {
    if (candidate.url().includes(`/api/projects/${staleProjectId}/`)) {
      staleRequests.push(candidate.url());
    }
  });

  await page.goto("/?page=board", { waitUntil: "domcontentloaded" });
  const wizard = page.getByTestId("welcome-wizard");
  const welcomeVisible = await wizard.waitFor({ state: "visible", timeout: 3_000 })
    .then(() => true)
    .catch(() => false);
  if (welcomeVisible && actionToken) await page.getByTestId("welcome-skip").click();

  await expect.poll(() => page.evaluate(() => (
    window.localStorage.getItem("zf.activeProjectId")
  ))).toBe(expectedProjectId);
  await expect.poll(() => new URL(page.url()).searchParams.get("project"))
    .toBe(expectedProjectId);
  await expect(page.getByLabel("Project")).toHaveValue(expectedProjectId);

  staleRequests.length = 0;
  await page.reload({ waitUntil: "domcontentloaded" });
  await expect(page.getByLabel("Project")).toHaveValue(expectedProjectId);
  expect(staleRequests).toEqual([]);
});
