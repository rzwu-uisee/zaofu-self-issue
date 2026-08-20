import { expect, test, type Page, type Route } from "@playwright/test";

const feature = {
  id: "F-GOAL-EVAL",
  title: "Goal coverage evaluation",
  status: "in_progress",
  priority: 1,
};

const secondFeature = {
  id: "F-SECOND",
  title: "Second delivery feature",
  status: "in_progress",
  priority: 2,
};

const graph = {
  schema_version: "goal-coverage-graph.v2",
  coverage_mode: "explicit",
  identity: {
    project_id: "default",
    workflow_run_id: "RUN-EVAL-18",
    goal_id: "F-GOAL-EVAL",
    task_map_generation: "GEN-3",
    task_map_ref: ".zf/artifacts/F-GOAL-EVAL/task_map.json",
    goal_claim_set_digest: "claim-set-digest",
    target_commit: "abcdef123456",
  },
  currentness: { is_current_generation: true, superseded_by: "", stale_reasons: [] },
  summary: {
    mandatory_claims: 3,
    planned_claims: 2,
    claims_with_current_results: 1,
    closed_claims: 1,
    open_gaps: 1,
  },
  nodes: [
    { node_id: "goal:F-GOAL-EVAL", kind: "goal", title: "Ship deterministic authentication", goal_id: "F-GOAL-EVAL", status: "rejected" },
    { node_id: "claim:CLAIM-AUTH", kind: "goal_claim", goal_claim_id: "CLAIM-AUTH", title: "Unauthorized actions are rejected", mandatory: true, source_ref: "objective.acceptance[0]", plan_coverage: "covered", execution: "done", task_verification: "passed", closure: "closed", task_ids: ["TASK-AUTH"], task_details: { total: 1, included: 1, missing_count: 0, task_ids_returned: 1, task_ids_truncated: false }, supporting_result_refs: ["artifact://verify-auth"], gap_refs: [] },
    { node_id: "claim:CLAIM-REPLAY", kind: "goal_claim", goal_claim_id: "CLAIM-REPLAY", title: "Replay remains deterministic across restart", mandatory: true, source_ref: "objective.acceptance[1]", plan_coverage: "covered", execution: "running", task_verification: "stale", closure: "open", task_ids: ["TASK-AUTH", "TASK-REPLAY"], task_details: { total: 2, included: 2, missing_count: 0, task_ids_returned: 2, task_ids_truncated: false }, supporting_result_refs: [], gap_refs: ["artifact://gap-replay"] },
    { node_id: "claim:CLAIM-MIGRATION", kind: "goal_claim", goal_claim_id: "CLAIM-MIGRATION", title: "Existing projects migrate without manual state edits", mandatory: true, source_ref: "objective.acceptance[2]", plan_coverage: "uncovered", execution: "pending", task_verification: "unverified", closure: "open", task_ids: [], task_details: { total: 0, included: 0, missing_count: 0, task_ids_returned: 0, task_ids_truncated: false }, supporting_result_refs: [], gap_refs: ["artifact://gap-replay"] },
    { node_id: "task:TASK-AUTH", kind: "task", task_id: "TASK-AUTH", title: "Implement authorization boundary", status: "done", owner: "dev-core", contract_revision: "REV-2", goal_claim_ids: ["CLAIM-AUTH", "CLAIM-REPLAY"] },
    { node_id: "task:TASK-REPLAY", kind: "task", task_id: "TASK-REPLAY", title: "Verify restart replay", status: "in_progress", owner: "verify-1", contract_revision: "REV-3", goal_claim_ids: ["CLAIM-REPLAY"] },
    { node_id: "result:artifact://verify-auth", kind: "verification_result", task_id: "TASK-AUTH", title: "Authorization checks passed", status: "passed", result_ref: "artifact://verify-auth", evidence_refs: ["artifact://auth-proof-1", "artifact://auth-proof-2", "artifact://auth-proof-3", "artifact://auth-proof-4"], current: true },
    { node_id: "closure:current", kind: "goal_closure", title: "Migration claim remains open", status: "rejected", result_ref: "current" },
    { node_id: "gap:artifact://gap-replay", kind: "gap", title: "artifact://gap-replay", status: "open", gap_ref: "artifact://gap-replay" },
  ],
  edges: [],
  diagnostics: [{ code: "mandatory_claim_uncovered", goal_claim_id: "CLAIM-MIGRATION", message: "mandatory claim has no covering task" }],
};

function graphForFeature(featureId: string) {
  if (featureId === feature.id) return graph;
  return {
    ...graph,
    identity: {
      ...graph.identity,
      workflow_run_id: "RUN-SECOND-1",
      goal_id: featureId,
      task_map_ref: `.zf/artifacts/${featureId}/task_map.json`,
    },
    nodes: graph.nodes.map((node) => node.kind === "goal"
      ? {
          ...node,
          node_id: `goal:${featureId}`,
          title: "Ship the second delivery feature",
          goal_id: featureId,
        }
      : node),
  };
}

type DeliveryTraceResponder = (
  route: Route,
  body: Record<string, unknown>,
  requestNumber: number,
) => Promise<void>;

async function installFixture(
  page: Page,
  theme: "light" | "dark",
  deliveryTraceResponder?: DeliveryTraceResponder,
  options: {
    capturePendingOnce?: boolean;
    captureTransportLossOnce?: boolean;
    regressionCasesFail?: boolean;
    replayPendingOnce?: boolean;
    replayRetrySequence?: boolean;
  } = {},
) {
  await page.addInitScript((mode) => {
    window.localStorage.setItem("zf.themeMode", mode);
    (window as unknown as { __zfFullscreenCalls: number }).__zfFullscreenCalls = 0;
    const original = Element.prototype.requestFullscreen;
    Element.prototype.requestFullscreen = function requestFullscreen(...args) {
      (window as unknown as { __zfFullscreenCalls: number }).__zfFullscreenCalls += 1;
      return original.apply(this, args);
    };
  }, theme);
  const workspaceProject = {
    project_id: "default",
    name: "Delivery fixture",
    root: "/tmp/zf-delivery-fixture",
    config_path: "/tmp/zf-delivery-fixture/zf.yaml",
    state_dir_hint: ".zf",
    can_open_board: true,
  };
  await page.route("**/api/workspace/projects", async (route) => {
    await route.fulfill({
      contentType: "application/json",
      body: JSON.stringify({
        schema_version: "workspace-projects.v1",
        active_project_id: "default",
        server_default_project_id: "default",
        items: [workspaceProject],
        projects: [workspaceProject],
      }),
    });
  });
  await page.route("**/api/workspace/onboarding", async (route) => {
    await route.fulfill({
      contentType: "application/json",
      body: JSON.stringify({
        schema_version: "workspace-onboarding.v1",
        show_welcome: false,
        completed: true,
        skipped: false,
        step: 0,
        backend: "",
        primary_backend: "",
        mixed_enabled: false,
        mixed_available: false,
        notifications: "disabled",
        backends: [],
        preflight: [],
      }),
    });
  });
  await page.route("**/api/web-session", async (route) => {
    await route.fulfill({
      contentType: "application/json",
      body: JSON.stringify({
        mode: "read_only",
        unlocked: false,
        actions_enabled: false,
      }),
    });
  });
  await page.route("**/api/projects/*/delivery-features", async (route) => {
    await route.fulfill({
      contentType: "application/json",
      body: JSON.stringify({
        delivery_features: [feature, secondFeature],
        features: [feature, secondFeature],
      }),
    });
  });
  let deliveryTraceRequestCount = 0;
  let overviewPulseRequestCount = 0;
  let workflowGraphRequestCount = 0;
  const actionRequests: Array<{ action: string; body: Record<string, unknown> }> = [];
  let captureRequestCount = 0;
  let replayRequestCount = 0;
  await page.route("**/api/projects/*/delivery-traces/*", async (route) => {
    const requestUrl = new URL(route.request().url());
    const requestedFeatureId = decodeURIComponent(requestUrl.pathname.split("/").at(-1) ?? feature.id);
    const view = requestUrl.searchParams.get("view") ?? "overview";
    const summaryExecutionGraph = {
      task_count: 2,
      done_count: 1,
      in_progress_count: 1,
      blocked_count: 0,
      waiting_count: 0,
      nodes: [],
      edges: [],
      waves: [],
      summary_only: true,
      basis: "canonical_task_state",
    };
    const common: Record<string, unknown> = {
        schema_version: "delivery-trace.v2",
        view,
        generated_at: "2026-07-22T04:21:00Z",
        project_id: "default",
        feature_id: requestedFeatureId,
        as_of_seq: 42,
        as_of_event_id: "evt-initial",
        refresh_scope: {
          task_ids: ["TASK-AUTH", "TASK-REPLAY"],
          task_ids_total: 2,
          task_ids_included: 2,
          task_ids_omitted: 0,
          task_ids_truncated: false,
        },
        status: "in_progress",
        task_map: {
          status: "accepted",
          task_map_ref: `.zf/artifacts/${requestedFeatureId}/task_map.json`,
          task_count: 2,
          wave_count: 2,
        },
        run_summary: { total: 2, completed: 1, running: 1, failed: 0, latest_label: "Verify restart replay · running" },
        cursor: {
          schema_version: "delivery-view-cursor.v2",
          last_event_id: "evt-initial",
          last_seq: 42,
          since_event_id: "",
          since_seq: null,
          new_event_count: 0,
          has_more: false,
          degraded: false,
          reason: "",
          delta_bodies_included: false,
        },
        deltas: [],
    };
    const runs: Record<string, unknown> = {
        ...common,
        canonical_trace_refs: [
          { trace_id: "trace-auth-canonical", task_ids: ["TASK-AUTH"], membership: "trace-v2-source-event" },
          { trace_id: "trace-other-task", task_ids: ["TASK-OTHER"], membership: "trace-v2-source-event" },
        ],
        canonical_trace_refs_truncated: false,
        execution_graph: summaryExecutionGraph,
        drift_report: { status: "not_evaluated", summary: {}, items: [], summary_only: true },
        ship: {
          status: "not_evaluated",
          readiness: "ship gates are not evaluated in this view",
          shipped: false,
          ship_status: "not_evaluated",
          required_tasks: 2,
          done_tasks: 1,
          missing_evidence: [],
          release_blockers: [],
          summary_only: true,
        },
        run_chain: {
          schema_version: "run-chain.v2",
          status: "in_progress",
          trigger: { event_id: "evt-plan", type: "task_map.ready", actor: "planner", ts: "2026-07-22T04:20:00Z" },
          stages: [{
            stage: "implementation",
            status: "active",
            entered_at: "2026-07-22T04:20:01Z",
            via_event_id: "evt-plan",
            causation_id: "evt-plan",
            seq_first: 5,
            seq_last: 18,
            occurrences: 1,
            task_ids: ["TASK-AUTH", "TASK-REPLAY"],
          }],
        },
        task_flow: {
          schema_version: "delivery-task-flow.v2",
          stage_order: ["implementation"],
          active_stage_ids: ["implementation"],
          stages: [],
          metrics: {},
          stages_truncated: false,
        },
        run_groups: [],
        task_lifecycle: {
          schema_version: "task-lifecycle.v2",
          task_count: 2,
          tasks_included: 2,
          tasks_omitted: 0,
          tasks_truncated: false,
          task_statuses: { "TASK-AUTH": "done", "TASK-REPLAY": "in_progress" },
          task_status_count: 2,
          task_statuses_included: 2,
          task_statuses_truncated: false,
          tasks: {
            "TASK-AUTH": {
              state_history_total: 4,
              state_history_included: 4,
              state_history_truncated: false,
              tries_total: 2,
              tries_included: 2,
              tries_truncated: false,
              gate_results_total: 2,
              gate_results_included: 2,
              gate_results_truncated: false,
              state_history: [
                { state: "running", entered_at: "2026-07-22T04:20:02Z", try: 1 },
                { state: "failed", entered_at: "2026-07-22T04:20:05Z", try: 1 },
                { state: "running", entered_at: "2026-07-22T04:20:06Z", try: 2 },
                { state: "done", entered_at: "2026-07-22T04:20:10Z", try: 2 },
              ],
              tries: [
                { try: 1, outcome: "failed", dispatch_id: "dispatch-auth-1", first_response_seconds: 1, gate_results: [{ type: "verify", passed: false, event_id: "evt-verify-1" }] },
                { try: 2, outcome: "done", dispatch_id: "dispatch-auth-2", first_response_seconds: 1, rework_kind: "verify_rework", gate_results: [{ type: "verify", passed: true, event_id: "evt-verify-2" }] },
              ],
            },
            "TASK-REPLAY": {
              state_history_total: 1,
              state_history_included: 1,
              state_history_truncated: false,
              tries_total: 1,
              tries_included: 1,
              tries_truncated: false,
              gate_results_total: 0,
              gate_results_included: 0,
              gate_results_truncated: false,
              state_history: [{ state: "running", entered_at: "2026-07-22T04:20:11Z", try: 1 }],
              tries: [{ try: 1, outcome: "in_flight", dispatch_id: "dispatch-replay-1", gate_results: [] }],
            },
          },
        },
    };
    const workLifecycle = {
      schema_version: "task-lifecycle.v2",
      task_count: 2,
      tasks_included: 2,
      tasks_omitted: 0,
      tasks_truncated: false,
      task_statuses: { "TASK-AUTH": "done", "TASK-REPLAY": "in_progress" },
      task_status_count: 2,
      task_statuses_included: 2,
      task_statuses_omitted: 0,
      task_statuses_truncated: false,
      state_history_total: 5,
      state_history_included: 0,
      state_history_omitted: 5,
      state_history_truncated: true,
      tries_total: 3,
      tries_included: 3,
      tries_omitted: 0,
      tries_truncated: false,
      gate_results_total: 3,
      gate_results_included: 2,
      gate_results_omitted: 1,
      gate_results_truncated: true,
      tasks: {
        "TASK-AUTH": {
          state_history: [],
          state_history_total: 4,
          state_history_included: 0,
          state_history_omitted: 4,
          state_history_truncated: true,
          tries_total: 2,
          tries_included: 2,
          tries_omitted: 0,
          tries_truncated: false,
          gate_results_total: 2,
          gate_results_included: 2,
          gate_results_omitted: 0,
          gate_results_truncated: false,
          tries: [
            { try: 1, outcome: "failed", rework_kind: "", gate_results: [{ type: "verify", passed: false, event_id: "evt-verify-1" }] },
            { try: 2, outcome: "done", rework_kind: "verify_rework", gate_results: [{ type: "verify", passed: true, event_id: "evt-verify-2" }] },
          ],
        },
        "TASK-REPLAY": {
          state_history: [],
          state_history_total: 1,
          state_history_included: 0,
          state_history_omitted: 1,
          state_history_truncated: true,
          tries_total: 1,
          tries_included: 1,
          tries_omitted: 0,
          tries_truncated: false,
          gate_results_total: 1,
          gate_results_included: 0,
          gate_results_omitted: 1,
          gate_results_truncated: true,
          tries: [{
            try: 1,
            outcome: "in_flight",
            rework_kind: "",
            gate_results: [],
            gate_results_total: 1,
            gate_results_included: 0,
            gate_results_omitted: 1,
            gate_results_truncated: true,
          }],
        },
      },
    };
    const work: Record<string, unknown> = {
      ...common,
      execution_graph: {
        ...summaryExecutionGraph,
        schema_version: "execution-graph.v2",
        nodes_only: true,
        nodes_total: 2,
        nodes_included: 2,
        nodes_omitted: 0,
        nodes_truncated: false,
        nodes: [
          {
            task_id: "TASK-AUTH",
            title: "Implement authorization boundary",
            goal_claim_ids: ["CLAIM-AUTH", "CLAIM-REPLAY"],
            planned: { owner_role: "dev", owner_instance: "dev-core", blocked_by: [] },
            actual: { status: "done", assigned_to: "dev-core", evidence_events: ["evt-auth-1", "evt-auth-2", "evt-auth-3", "evt-auth-4"] },
            drift: [],
          },
          {
            task_id: "TASK-REPLAY",
            title: "Verify restart replay",
            goal_claim_ids: ["CLAIM-REPLAY"],
            planned: { owner_role: "verify", owner_instance: "verify-1", blocked_by: ["TASK-AUTH"] },
            actual: { status: "in_progress", assigned_to: "verify-1", evidence_events: [] },
            drift: [],
          },
        ],
        edges: [],
        waves: [],
      },
      drift_report: { status: "not_evaluated", summary: {}, items: [], summary_only: true },
      ship: {
        status: "not_evaluated",
        readiness: "ship gates are not evaluated in this view",
        shipped: false,
        ship_status: "not_evaluated",
        required_tasks: 2,
        done_tasks: 1,
        missing_evidence: [],
        release_blockers: [],
        summary_only: true,
        basis: "not_computed_for_work_view",
      },
      goal_coverage_graph: graphForFeature(requestedFeatureId),
      task_lifecycle: workLifecycle,
    };
    const body: Record<string, unknown> = view === "runs"
      ? runs
      : view === "work"
        ? work
      : view === "graph"
        ? {
            ...common,
            execution_graph: {
              ...summaryExecutionGraph,
              schema_version: "execution-graph.v2",
              nodes_truncated: true,
              edges_truncated: true,
            },
            drift_report: { status: "warning", summary: { warning: 1 }, items: [] },
            ship: {
              status: "blocked",
              shipped: false,
              required_tasks: 2,
              done_tasks: 1,
              missing_evidence: [{ task_id: "TASK-REPLAY", status: "in_progress" }],
              release_blockers: [],
            },
            goal_coverage_graph: graphForFeature(requestedFeatureId),
          }
        : {
            ...common,
            execution_graph: summaryExecutionGraph,
            drift_report: { status: "warning", summary: { warning: 1 }, items: [], summary_only: true },
            ship: {
              status: "blocked",
              shipped: false,
              required_tasks: 2,
              done_tasks: 1,
              missing_evidence: [{ task_id: "TASK-REPLAY", status: "in_progress" }],
              release_blockers: [],
            },
            attention: [],
            attention_summary: { total_count: 0, truncated: false, by_kind: [], by_kind_truncated: false },
          };
    deliveryTraceRequestCount += 1;
    if (deliveryTraceResponder) {
      await deliveryTraceResponder(route, body, deliveryTraceRequestCount);
      return;
    }
    await route.fulfill({
      contentType: "application/json",
      body: JSON.stringify(body),
    });
  });
  await page.route("**/api/projects/*/workflow/graph", async (route) => {
    workflowGraphRequestCount += 1;
    await route.fulfill({
      contentType: "application/json",
      body: JSON.stringify({
        schema_version: "workflow-graph.v1",
        nodes: [{
          id: "role:verify",
          kind: "role",
          label: "verify",
          pass_rate: 0.5,
          rework_count: 1,
          drill_task_id: "TASK-REPLAY",
        }],
        edges: [],
      }),
    });
  });
  await page.route("**/api/projects/*/overview-pulse", async (route) => {
    overviewPulseRequestCount += 1;
    await route.fulfill({ contentType: "application/json", body: JSON.stringify({}) });
  });
  await page.route("**/api/projects/*/regression-cases?*", async (route) => {
    if (options.regressionCasesFail) {
      await route.fulfill({
        status: 500,
        contentType: "application/json",
        body: JSON.stringify({ reason: "regression store unavailable" }),
      });
      return;
    }
    await route.fulfill({
      contentType: "application/json",
      body: JSON.stringify({
        cases: [{
          case_id: "REG-REPLAY",
          source_task_id: "TASK-REPLAY",
          feature_id: feature.id,
          assertions: ["rework==0"],
        }],
      }),
    });
  });
  await page.route("**/api/projects/*/actions/*", async (route) => {
    const action = new URL(route.request().url()).pathname.split("/").at(-1) ?? "";
    const body = route.request().postDataJSON() as Record<string, unknown>;
    actionRequests.push({ action, body });
    if (action === "capture-regression-case") {
      captureRequestCount += 1;
      await new Promise((resolve) => setTimeout(resolve, 100));
      if (captureRequestCount === 1) {
        if (options.captureTransportLossOnce) {
          await route.abort("connectionreset");
          return;
        }
        if (options.capturePendingOnce) {
          await route.fulfill({
            status: 202,
            contentType: "application/json",
            body: JSON.stringify({ ok: true, status: "duplicate_pending", action, reason: "still processing" }),
          });
          return;
        }
        await route.fulfill({
          status: 409,
          contentType: "application/json",
          body: JSON.stringify({ ok: false, status: "rejected", action, reason: "capture rejected by gate" }),
        });
        return;
      }
    }
    if (action === "replay-regression-case") {
      replayRequestCount += 1;
      await new Promise((resolve) => setTimeout(resolve, 100));
      if (options.replayRetrySequence && replayRequestCount === 1) {
        await route.abort("connectionreset");
        return;
      }
      if (options.replayRetrySequence && replayRequestCount === 2) {
        await route.fulfill({
          status: 409,
          contentType: "application/json",
          body: JSON.stringify({ ok: false, status: "rejected", action, reason: "replay rejected by gate" }),
        });
        return;
      }
      if (options.replayPendingOnce && replayRequestCount === 1) {
        await route.fulfill({
          status: 202,
          contentType: "application/json",
          body: JSON.stringify({ ok: true, status: "duplicate_pending", action, reason: "still processing" }),
        });
        return;
      }
    }
    await route.fulfill({
      contentType: "application/json",
      body: JSON.stringify({ ok: true, status: "accepted", action, reason: "", result: { passed: true } }),
    });
  });
  return {
    actionRequests: () => actionRequests,
    deliveryTraceRequestCount: () => deliveryTraceRequestCount,
    overviewPulseRequestCount: () => overviewPulseRequestCount,
    workflowGraphRequestCount: () => workflowGraphRequestCount,
  };
}

async function openCoverage(page: Page) {
  await page.goto("/?page=goal-coverage");
  await expect(page.getByTestId("goal-coverage-page")).toBeVisible({ timeout: 30_000 });
  await expect(page.getByTestId("goal-coverage-claim-row")).toHaveCount(3);
}

async function expectNoPageOverflow(page: Page) {
  const geometry = await page.evaluate(() => ({
    clientWidth: document.documentElement.clientWidth,
    scrollWidth: document.documentElement.scrollWidth,
    viewportWidth: window.innerWidth,
  }));
  expect(geometry.scrollWidth).toBeLessThanOrEqual(geometry.clientWidth + 1);
  expect(geometry.clientWidth).toBeLessThanOrEqual(geometry.viewportWidth);
}

test("desktop light goal coverage supports selection, search, and focus", async ({ page }, testInfo) => {
  await page.setViewportSize({ width: 1440, height: 960 });
  await installFixture(page, "light");
  await openCoverage(page);

  await expect(page.locator("html")).toHaveAttribute("data-theme", "light");
  await expect(page.getByTestId("goal-coverage-summary")).toContainText("2/3");
  await expect(page.getByLabel("Current generation")).toContainText("GEN-3");
  await expect(page.getByLabel("Current generation")).toContainText("current");
  await expect(page.getByLabel("Generation", { exact: true })).toHaveCount(0);
  await page.locator('[data-claim-id="CLAIM-AUTH"] .goal-coverage-claim-node').click();
  await expect(page.getByTestId("goal-coverage-inspector")).toContainText("Unauthorized actions are rejected");
  await expect(page.getByTestId("goal-coverage-inspector")).toContainText("artifact://verify-auth");
  await page.getByLabel("Search claims and tasks").fill("migration");
  await expect(page.getByTestId("goal-coverage-claim-row")).toHaveCount(1);
  await expect(page.getByTestId("goal-coverage-inspector")).toContainText("No covering task");

  await page.getByRole("button", { name: "Enter focus mode" }).click();
  const focused = page.getByTestId("goal-coverage-page");
  await expect(focused).toHaveClass(/is-focus/);
  const focusedBox = await focused.boundingBox();
  expect(focusedBox?.x).toBeLessThanOrEqual(1);
  expect(focusedBox?.width).toBeGreaterThanOrEqual(1438);
  await page.keyboard.press("Escape");
  await expect(focused).not.toHaveClass(/is-focus/);
  await expect(page.getByTestId("goal-coverage-inspector")).toContainText("CLAIM-MIGRATION");
  expect(await page.evaluate(() => (window as unknown as { __zfFullscreenCalls: number }).__zfFullscreenCalls)).toBe(0);
  await expectNoPageOverflow(page);
  await page.screenshot({ path: testInfo.outputPath("goal-coverage-desktop-light.png"), fullPage: true });

  await page.getByLabel("Search claims and tasks").fill("");
  await page.locator('[data-claim-id="CLAIM-AUTH"] .goal-coverage-claim-node').click();
  await page.getByTestId("goal-coverage-inspector").getByRole("button", { name: /Implement authorization boundary/ }).click();
  await expect(page).toHaveURL(/page=task/);
  await expect(page).toHaveURL(/task=TASK-AUTH/);
});

test("mobile dark goal coverage renders outline without horizontal overflow", async ({ page }, testInfo) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await installFixture(page, "dark");
  await openCoverage(page);

  await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
  const firstRow = page.getByTestId("goal-coverage-claim-row").first();
  const tracks = await firstRow.evaluate((node) => getComputedStyle(node).gridTemplateColumns);
  expect(tracks.trim().split(/\s+/)).toHaveLength(1);
  await expect(firstRow.getByText("Claim", { exact: true })).toBeVisible();
  await expect(firstRow.getByText("Plan", { exact: true })).toBeVisible();
  await expect(firstRow.getByText("Implementation", { exact: true })).toBeVisible();
  await expect(firstRow.getByText("Verification", { exact: true })).toBeVisible();
  await expect(firstRow.getByText("Closure", { exact: true })).toBeVisible();
  await expectNoPageOverflow(page);
  await page.screenshot({ path: testInfo.outputPath("goal-coverage-mobile-dark.png"), fullPage: true });
});

test("Graph keeps Coverage light and loads the Work tree on demand", async ({ page }, testInfo) => {
  await page.setViewportSize({ width: 1440, height: 960 });
  await page.addInitScript(() => {
    let visibility: DocumentVisibilityState = "visible";
    Object.defineProperty(document, "visibilityState", {
      configurable: true,
      get: () => visibility,
    });
    const sources: Array<{
      closed: boolean;
      onerror: ((event: Event) => void) | null;
    }> = [];
    class WorkEventSource {
      onopen: ((event: Event) => void) | null = null;
      onerror: ((event: Event) => void) | null = null;
      onmessage: ((event: MessageEvent) => void) | null = null;
      closed = false;

      constructor() {
        sources.push(this);
        window.setTimeout(() => this.onopen?.(new Event("open")), 0);
      }

      addEventListener() {}
      close() { this.closed = true; }
    }
    Object.defineProperty(window, "EventSource", { configurable: true, value: WorkEventSource });
    const controls = window as unknown as {
      __zfDisconnectWork: () => void;
      __zfSetWorkVisibility: (state: DocumentVisibilityState) => void;
    };
    controls.__zfDisconnectWork = () => {
        for (const source of sources) {
        if (!source.closed) source.onerror?.(new Event("error"));
        }
    };
    controls.__zfSetWorkVisibility = (state) => {
      visibility = state;
      document.dispatchEvent(new Event("visibilitychange"));
    };
  });
  let graphGeneration = 0;
  let workGeneration = 0;
  let releaseFirstWork = () => {};
  const firstWorkGate = new Promise<void>((resolve) => {
    releaseFirstWork = resolve;
  });
  const opaqueTaskId = `task-ref:sha256:${"a".repeat(64)}`;
  const requests = await installFixture(page, "light", async (route, body) => {
    const next = structuredClone(body);
    if (next.view === "graph") {
      graphGeneration += 1;
      next.generated_at = `2026-07-22T04:21:0${graphGeneration}Z`;
      (next.ship as Record<string, unknown>).status = "unknown";
      const coverage = next.goal_coverage_graph as {
        nodes: Array<Record<string, unknown>>;
      };
      coverage.nodes = coverage.nodes.map((node) => {
        if (node.kind === "goal") return { ...node, status: "unknown" };
        return node.goal_claim_id === "CLAIM-AUTH" ? {
            ...node,
            task_ids: [...(node.task_ids as string[]), opaqueTaskId],
            task_details: { total: 2, included: 2, missing_count: 0 },
          }
          : node;
      });
      coverage.nodes.push({
        node_id: `task:${opaqueTaskId}`,
        kind: "task",
        task_id: opaqueTaskId,
        task_id_opaque: true,
        title: "Opaque identity task",
        status: "blocked",
        goal_claim_ids: ["CLAIM-AUTH"],
      });
    } else if (next.view === "work") {
      workGeneration += 1;
      if (workGeneration === 1) await firstWorkGate;
      const execution = next.execution_graph as {
        nodes: Array<Record<string, unknown>>;
        task_count: number;
        blocked_count: number;
        nodes_total: number;
        nodes_included: number;
      };
      execution.task_count = 3;
      execution.blocked_count = 1;
      execution.nodes_total = 3;
      execution.nodes_included = 3;
      execution.nodes.push({
        task_id: opaqueTaskId,
        task_id_opaque: true,
        title: "Opaque identity task",
        goal_claim_ids: [],
        planned: { owner_role: "dev", owner_instance: "dev-long", blocked_by: [] },
        actual: { status: "blocked", assigned_to: "dev-long", evidence_events: [] },
        drift: [],
      });
      const workGoal = next.goal_coverage_graph as {
        nodes: Array<Record<string, unknown>>;
      };
      workGoal.nodes = workGoal.nodes.map((node) => {
        if (node.kind === "goal") return { ...node, status: "unknown" };
        return node.goal_claim_id === "CLAIM-MIGRATION" ? {
            ...node,
            plan_coverage: "covered",
            task_ids: [],
            task_ids_total: 1,
            task_ids_included: 0,
            task_ids_omitted: 1,
            task_ids_truncated: true,
            task_details: { total: 1, included: 0, missing_count: 1 },
          }
          : node;
      });
      if (workGeneration > 1) {
        execution.nodes = execution.nodes.map((node) => node.task_id === "TASK-REPLAY"
          ? { ...node, actual: { ...(node.actual as Record<string, unknown>), status: "blocked" } }
          : node);
        const lifecycle = next.task_lifecycle as {
          task_statuses: Record<string, string>;
        };
        lifecycle.task_statuses = { ...lifecycle.task_statuses, "TASK-REPLAY": "blocked" };
      }
    }
    await route.fulfill({ contentType: "application/json", body: JSON.stringify(next) });
  });
  const deliveryUrls: string[] = [];
  const workAssetUrls: string[] = [];
  page.on("request", (request) => {
    if (request.url().includes("/delivery-traces/")) deliveryUrls.push(request.url());
    if (request.url().includes("DeliveryWorkView")) workAssetUrls.push(request.url());
  });
  await page.goto("/?page=delivery-graph");

  await expect(page.getByRole("heading", { name: "Graph", exact: true })).toBeVisible();
  await expect(page.getByRole("tablist", { name: "Graph view" })).toBeVisible();
  await expect(page.getByRole("tab", { name: "Coverage" })).toHaveAttribute("aria-selected", "true");
  await expect(page.locator(".delivery-cockpit-metric > span").filter({ hasText: /^Ship$/ })).toHaveCount(0);
  const gateMetric = page.locator(".delivery-cockpit-metric").filter({ hasText: /^Gate/ });
  await expect(gateMetric).toContainText("drift:warning");
  await expect(page.getByTestId("goal-coverage-claim-row")).toHaveCount(3);
  await expect(page.getByTestId("goal-coverage-inspector")).toBeVisible();
  await expect(page.getByText("Plan", { exact: true }).first()).toBeVisible();
  await expect(page.getByText("Implementation", { exact: true }).first()).toBeVisible();
  await expect(page.getByText("Verification", { exact: true }).first()).toBeVisible();
  await expect(page.getByText("Closure", { exact: true }).first()).toBeVisible();
  await expect(page.getByTestId("delivery-map-work")).toHaveCount(0);
  await expect(page.getByTestId("delivery-map-diagnostics")).toHaveCount(0);
  expect(requests.workflowGraphRequestCount()).toBe(0);
  expect(deliveryUrls.some((value) => value.includes("contract=v2") && value.includes("view=graph"))).toBe(true);
  expect(deliveryUrls.some((value) => value.includes("view=work"))).toBe(false);
  expect(workAssetUrls).toHaveLength(0);
  await page.locator('[data-claim-id="CLAIM-AUTH"] .goal-coverage-claim-node').click();
  const opaqueCoverageRow = page.getByTestId("goal-coverage-inspector").getByRole("button")
    .filter({ hasText: opaqueTaskId });
  await expect(opaqueCoverageRow).toBeDisabled();
  await expect(page.getByTestId("goal-coverage-task-id-opaque"))
    .toContainText("canonical task ID was omitted");

  await page.getByRole("tab", { name: "Work" }).click();
  await expect(page.getByTestId("delivery-work-goal-picker")).toBeVisible();
  await expect(page.getByText("Select a Goal to load Work", { exact: true })).toBeVisible();
  await expect(page.getByTestId("delivery-work-goal-picker").getByText("not evaluated", { exact: true })).toHaveCount(0);
  await expect(page.getByTestId("delivery-work-expand-goal")).toHaveCount(0);
  expect(deliveryUrls.filter((value) => value.includes("view=work"))).toHaveLength(0);
  expect(workAssetUrls).toHaveLength(0);
  const workGoal = page.getByRole("radio", { name: /Ship deterministic authentication/ });
  await workGoal.click();
  await expect(workGoal).toBeChecked();
  const workLoading = page.getByTestId("delivery-work-loading");
  await expect(workLoading).toBeVisible();
  await expect(workLoading).toHaveAttribute("aria-busy", "true");
  await expect(workLoading).toContainText("Loading selected Goal…");
  await expect(workLoading.locator(".delivery-map-loading-spinner")).toBeVisible();
  await expect(page.getByTestId("delivery-map-work")).toHaveCount(0);
  releaseFirstWork();
  await expect(page.getByTestId("delivery-map-work")).toBeVisible();
  await expect(workLoading).toHaveCount(0);
  await expect(page.locator('[data-work-kind="goal"] .goal-coverage-status')).toHaveCount(0);
  await expect(page.getByTestId("delivery-work-progressive").getByText("not evaluated", { exact: true })).toHaveCount(0);
  await expect(page.getByTestId("delivery-work-progressive").getByText("unknown", { exact: true })).toHaveCount(0);
  expect(workAssetUrls.length).toBeGreaterThan(0);
  await expect(page.getByTestId("delivery-work-node")).toHaveCount(8);
  await expect(page.getByTestId("delivery-work-node").first()).toBeVisible();
  const workCanvasBox = await page.getByTestId("delivery-work-canvas").boundingBox();
  expect(workCanvasBox?.height).toBeGreaterThan(280);
  await expect(page.getByText("also covers", { exact: true })).toBeVisible();
  expect(deliveryUrls.filter((value) => value.includes("view=work"))).toHaveLength(1);
  expect(deliveryUrls.find((value) => value.includes("view=work"))).toContain("goal_id=F-GOAL-EVAL");
  await page.getByLabel("Search Work tree").fill("restart replay");
  await expect(page.locator('[data-work-node-id="task:TASK-REPLAY"]')).toHaveClass(/is-match/);
  await expect(page.getByTestId("delivery-work-inspector")).toContainText("Blocked by");
  await expect(page.getByTestId("delivery-work-inspector")).toContainText("TASK-AUTH");
  await expect(page.getByTestId("delivery-work-inspector")).toContainText("Try #1");
  await expect(page.getByTestId("delivery-work-gates-truncated")).toHaveText("1 gate result omitted");
  await page.getByLabel("Search Work tree").fill("authorization");
  await expect(page.locator('[data-work-node-id="task:TASK-AUTH"]')).toHaveClass(/is-match/);
  await expect(page.getByTestId("delivery-work-inspector")).toContainText("artifact://auth-proof-4");
  await page.evaluate(() => {
    const controls = window as unknown as {
      __zfDisconnectWork: () => void;
      __zfSetWorkVisibility: (state: DocumentVisibilityState) => void;
    };
    controls.__zfSetWorkVisibility("hidden");
    controls.__zfDisconnectWork();
  });
  await page.waitForTimeout(400);
  await page.evaluate(() => {
    (window as unknown as {
      __zfSetWorkVisibility: (state: DocumentVisibilityState) => void;
    }).__zfSetWorkVisibility("visible");
  });
  await expect.poll(() => deliveryUrls.filter((value) => value.includes("view=work")).length).toBe(2);
  await page.getByLabel("Search Work tree").fill("restart replay");
  await expect(page.getByTestId("delivery-work-inspector")).toContainText("blocked");
  await page.getByLabel("Search Work tree").fill("migrate without manual");
  await expect(page.getByTestId("delivery-work-claim-tasks-omitted"))
    .toHaveText("Covering task details omitted by the bounded Work projection.");
  await page.getByLabel("Search Work tree").fill("opaque identity");
  await expect(page.getByTestId("delivery-work-task-id-opaque"))
    .toContainText("Canonical task ID was omitted");
  await expect(page.getByTestId("delivery-work-inspector").getByRole("button", {
    name: "Open canonical task",
  })).toHaveCount(0);
  expect(deliveryUrls.filter((value) => value.includes("view=work"))).toHaveLength(2);
  await page.getByRole("button", { name: secondFeature.title, exact: true }).click();
  await expect(page.getByRole("tab", { name: "Coverage" })).toHaveAttribute("aria-selected", "true");
  await expect(page.getByTestId("delivery-map-work")).toHaveCount(0);
  await page.getByRole("tab", { name: "Work" }).click();
  await expect(page.getByTestId("delivery-work-goal-picker")).toContainText("Ship the second delivery feature");
  await expect(page.getByRole("radio", { name: /Ship the second delivery feature/ })).not.toBeChecked();
  expect(deliveryUrls.filter((value) => value.includes("view=work"))).toHaveLength(2);
  await expectNoPageOverflow(page);
  await page.screenshot({ path: testInfo.outputPath("delivery-graph-coverage-desktop.png"), fullPage: true });
});

test("Delivery views bound live refreshes and Overview accepts task-only SSE", async ({ page }) => {
  test.setTimeout(65_000);
  await page.setViewportSize({ width: 1440, height: 960 });
  await page.addInitScript(() => {
    let visibility: DocumentVisibilityState = "visible";
    Object.defineProperty(document, "visibilityState", {
      configurable: true,
      get: () => visibility,
    });
    const sources: StableEventSource[] = [];
    let nextSeq = 1;
    class StableEventSource {
      onopen: ((event: Event) => void) | null = null;
      onerror: ((event: Event) => void) | null = null;
      onmessage: ((event: MessageEvent) => void) | null = null;
      closed = false;

      constructor(url: string | URL) {
        const cursor = Number(new URL(String(url), window.location.href).searchParams.get("cursor") ?? "0");
        if (Number.isFinite(cursor)) nextSeq = Math.max(nextSeq, cursor + 1);
        sources.push(this);
        window.setTimeout(() => this.onopen?.(new Event("open")), 0);
      }

      addEventListener() {}
      close() { this.closed = true; }
    }
    Object.defineProperty(window, "EventSource", { configurable: true, value: StableEventSource });
    const deliveryControls = window as unknown as {
      __zfDisconnectDelivery: () => void;
      __zfEmitDeliveryEvent: (event: Record<string, unknown>) => void;
      __zfSetVisibility: (state: DocumentVisibilityState) => void;
    };
    deliveryControls.__zfDisconnectDelivery = () => {
      for (const source of sources) {
        if (!source.closed) source.onerror?.(new Event("error"));
      }
    };
    deliveryControls.__zfEmitDeliveryEvent = (event) => {
      const streamedEvent = { ...event, seq: nextSeq };
      nextSeq += 1;
      for (const source of sources) {
        if (!source.closed) {
          source.onmessage?.(new MessageEvent("message", { data: JSON.stringify(streamedEvent) }));
        }
      }
    };
    (window as unknown as {
      __zfSetVisibility: (state: DocumentVisibilityState) => void;
    }).__zfSetVisibility = (state) => {
      visibility = state;
      document.dispatchEvent(new Event("visibilitychange"));
    };
  });
  let activeRequests = 0;
  let maxActiveRequests = 0;
  let releaseSecond!: () => void;
  const secondRequestGate = new Promise<void>((resolve) => { releaseSecond = resolve; });
  const requests = await installFixture(page, "light", async (route, body, requestNumber) => {
    activeRequests += 1;
    maxActiveRequests = Math.max(maxActiveRequests, activeRequests);
    try {
      if (requestNumber === 2) await secondRequestGate;
      const next = structuredClone(body);
      next.cursor = { last_event_id: `evt-${requestNumber}`, new_event_count: requestNumber > 1 ? 1 : 0 };
      await route.fulfill({ contentType: "application/json", body: JSON.stringify(next) });
    } finally {
      activeRequests -= 1;
    }
  });
  await page.goto("/?page=delivery-graph");

  await expect(page.getByTestId("goal-coverage-goal-node")).toContainText("Ship deterministic authentication");
  expect(requests.deliveryTraceRequestCount()).toBe(1);
  expect(requests.workflowGraphRequestCount()).toBe(0);
  await page.waitForTimeout(15_250);
  expect(requests.deliveryTraceRequestCount()).toBe(1);
  expect(requests.workflowGraphRequestCount()).toBe(0);

  const emit = (event: Record<string, unknown>) => page.evaluate((value) => {
    (window as unknown as { __zfEmitDeliveryEvent: (row: Record<string, unknown>) => void })
      .__zfEmitDeliveryEvent(value);
  }, event);
  await emit({ type: "run.manager.tick.completed", seq: 100, payload: {} });
  await page.waitForTimeout(400);
  expect(requests.deliveryTraceRequestCount()).toBe(1);

  await Promise.all([
    emit({ type: "feature.updated", seq: 101, payload: { feature_id: feature.id } }),
    emit({ type: "feature.updated", seq: 102, payload: { feature_id: feature.id } }),
    emit({ type: "feature.updated", seq: 103, payload: { feature_id: feature.id } }),
  ]);
  await expect.poll(() => requests.deliveryTraceRequestCount()).toBe(2);
  await emit({ type: "feature.updated", seq: 104, payload: { feature_id: feature.id } });
  await page.waitForTimeout(400);
  expect(requests.deliveryTraceRequestCount()).toBe(2);
  expect(maxActiveRequests).toBe(1);
  releaseSecond();
  await expect.poll(() => requests.deliveryTraceRequestCount()).toBe(3);
  await expect.poll(() => activeRequests).toBe(0);
  expect(maxActiveRequests).toBe(1);

  await page.evaluate(() => {
    (window as unknown as { __zfSetVisibility: (state: DocumentVisibilityState) => void })
      .__zfSetVisibility("hidden");
  });
  await emit({ type: "feature.updated", seq: 105, payload: { feature_id: feature.id } });
  await page.waitForTimeout(400);
  expect(requests.deliveryTraceRequestCount()).toBe(3);
  await page.evaluate(() => {
    (window as unknown as { __zfSetVisibility: (state: DocumentVisibilityState) => void })
      .__zfSetVisibility("visible");
  });
  await expect.poll(() => requests.deliveryTraceRequestCount()).toBe(4);
  expect(maxActiveRequests).toBe(1);

  await page.getByTestId("dt-mode-tabs").getByRole("button", { name: "Overview" }).click();
  await expect(page.getByRole("heading", { name: "Delivery", exact: true })).toBeVisible();
  await expect.poll(() => requests.deliveryTraceRequestCount()).toBe(5);
  await emit({ type: "task.updated", task_id: "TASK-REPLAY", payload: {} });
  await expect.poll(() => requests.deliveryTraceRequestCount()).toBe(6);
  expect(maxActiveRequests).toBe(1);

  await page.evaluate(() => {
    const controls = window as unknown as {
      __zfDisconnectDelivery: () => void;
      __zfSetVisibility: (state: DocumentVisibilityState) => void;
    };
    controls.__zfSetVisibility("hidden");
    controls.__zfDisconnectDelivery();
  });
  await page.waitForTimeout(400);
  expect(requests.deliveryTraceRequestCount()).toBe(6);
  await page.evaluate(() => {
    (window as unknown as { __zfSetVisibility: (state: DocumentVisibilityState) => void })
      .__zfSetVisibility("visible");
  });
  await expect.poll(() => requests.deliveryTraceRequestCount()).toBe(7);
  await page.waitForTimeout(15_250);
  await expect.poll(() => requests.deliveryTraceRequestCount()).toBe(8);
  expect(maxActiveRequests).toBe(1);
});

test("Runs keeps one Run surface and scopes evidence to the selected task", async ({ page }, testInfo) => {
  await page.setViewportSize({ width: 1440, height: 960 });
  const requests = await installFixture(page, "light");
  const deliveryUrls: string[] = [];
  page.on("request", (request) => {
    if (request.url().includes("/delivery-traces/")) deliveryUrls.push(request.url());
  });
  await page.goto("/?page=delivery-trace");

  await expect(page.getByRole("heading", { name: "Runs", exact: true })).toBeVisible();
  await expect(page.getByTestId("dt-mode-tabs").getByRole("button")).toHaveText(["Overview", "Runs", "Graph"]);
  await expect(page.getByTestId("delivery-run-surface-head")).toContainText("Run");
  await expect(page.getByRole("tablist", { name: "Runs views" })).toHaveCount(0);
  await expect(page.getByTestId("delivery-trace-tab")).toHaveCount(0);
  await expect(page.getByTestId("graph-stage-heatmap")).toHaveCount(0);
  await expect(page.getByTestId("run-graph")).toBeVisible();
  await expect(page.getByTestId("rg-pool-strip")).toHaveCount(0);
  const runTypography = await page.getByTestId("run-graph").evaluate((graph) => ({
    meta: getComputedStyle(graph.querySelector<HTMLElement>(".rg-node-sub")!).fontWeight,
    task: getComputedStyle(graph.querySelector<HTMLElement>(".rg-task-id")!).fontWeight,
    title: getComputedStyle(graph.querySelector<HTMLElement>(".rg-node-name")!).fontWeight,
  }));
  expect(runTypography).toEqual({ meta: "400", task: "400", title: "500" });
  expect(requests.workflowGraphRequestCount()).toBe(0);
  expect(requests.overviewPulseRequestCount()).toBe(0);
  expect(deliveryUrls.some((value) => value.includes("contract=v2") && value.includes("view=runs"))).toBe(true);

  await page.getByTestId("rg-task-TASK-REPLAY").click();
  await expect(page.getByTestId("lifecycle-drawer")).toHaveAttribute("aria-label", "Task lifecycle TASK-REPLAY");
  await expect(page.getByTestId("lifecycle-drawer").locator(".ld-task-id")).toHaveCSS("font-weight", "500");
  await expect(page.getByTestId("run-open-trace")).toHaveCount(0);
  await expect(page.getByTestId("regression-cases")).toContainText("REG-REPLAY");
  await expect(page.getByTestId("run-capture-regression")).toHaveCount(0);
  await page.getByTestId("regression-replay-btn").click();
  await expect(page.getByTestId("regression-verdict")).toHaveText("Pass");
  await expect.poll(() => requests.actionRequests().length).toBe(1);
  expect(requests.actionRequests()[0]).toMatchObject({
    action: "replay-regression-case",
    body: { payload: { case_id: "REG-REPLAY" } },
  });
  await page.getByRole("button", { name: "close lifecycle drawer" }).click();

  await page.getByTestId("rg-task-TASK-AUTH").click();
  await expect(page.getByTestId("ld-tries")).toContainText("#1");
  await expect(page.getByTestId("ld-tries")).toContainText("#2");
  await expect(page.getByTestId("run-open-trace")).toBeVisible();
  await expect(page.getByTestId("run-open-trace")).toHaveText("Open in Trace");
  await page.getByTestId("run-capture-regression").evaluate((button) => {
    (button as HTMLButtonElement).click();
    (button as HTMLButtonElement).click();
  });
  await expect(page.getByTestId("run-regression-error")).toContainText("capture rejected by gate");
  await expect.poll(() => requests.actionRequests().length).toBe(2);
  await page.waitForTimeout(150);
  expect(requests.actionRequests()).toHaveLength(2);
  expect(requests.actionRequests()[1]).toMatchObject({
    action: "capture-regression-case",
    body: {
      payload: {
        task_id: "TASK-AUTH",
        feature_id: "F-GOAL-EVAL",
      },
    },
  });
  expect(
    ((requests.actionRequests()[1].body.payload as Record<string, unknown>).idempotency_key),
  ).toMatch(/^delivery-regression-capture:.+:F-GOAL-EVAL:TASK-AUTH:.+$/);

  await page.getByTestId("run-capture-regression").click();
  await expect(page.getByTestId("run-capture-regression")).toHaveText("Captured");
  await expect(page.getByTestId("run-capture-regression")).toBeDisabled();
  await expect.poll(() => requests.actionRequests().length).toBe(3);
  expect(requests.actionRequests()[2]).toMatchObject({
    action: "capture-regression-case",
    body: { payload: { task_id: "TASK-AUTH", feature_id: "F-GOAL-EVAL" } },
  });
  expect(
    (requests.actionRequests()[2].body.payload as Record<string, unknown>).idempotency_key,
  ).not.toBe((requests.actionRequests()[1].body.payload as Record<string, unknown>).idempotency_key);
  await page.screenshot({ path: testInfo.outputPath("delivery-runs-desktop.png"), fullPage: true });
});

test("Runs fails closed when regression cases cannot be loaded", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 960 });
  const requests = await installFixture(page, "light", undefined, { regressionCasesFail: true });
  await page.goto("/?page=delivery-trace&feature=F-GOAL-EVAL");

  await page.getByTestId("rg-task-TASK-AUTH").click();
  await expect(page.getByTestId("regression-cases-error")).toContainText("returned 500");
  await expect(page.getByText("No regression case captured for this task.")).toHaveCount(0);
  await expect(page.getByTestId("run-capture-regression")).toHaveCount(0);
  expect(requests.actionRequests()).toHaveLength(0);
});

test("Runs reuses the capture idempotency key after an unknown transport outcome", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 960 });
  const requests = await installFixture(page, "light", undefined, { captureTransportLossOnce: true });
  await page.goto("/?page=delivery-trace&feature=F-GOAL-EVAL");

  await page.getByTestId("rg-task-TASK-AUTH").click();
  await page.getByTestId("run-capture-regression").click();
  await expect(page.getByTestId("run-regression-error")).toBeVisible();
  await expect.poll(() => requests.actionRequests().length).toBe(1);
  const firstKey = (requests.actionRequests()[0].body.payload as Record<string, unknown>).idempotency_key;

  await page.getByTestId("run-capture-regression").click();
  await expect(page.getByTestId("run-capture-regression")).toHaveText("Captured");
  await expect.poll(() => requests.actionRequests().length).toBe(2);
  const retryKey = (requests.actionRequests()[1].body.payload as Record<string, unknown>).idempotency_key;
  expect(retryKey).toBe(firstKey);
});

test("Runs reuses an ambiguous replay key and rotates it after an explicit rejection", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 960 });
  const requests = await installFixture(page, "light", undefined, { replayRetrySequence: true });
  await page.goto("/?page=delivery-trace&feature=F-GOAL-EVAL");

  await page.getByTestId("rg-task-TASK-REPLAY").click();
  const replay = page.getByTestId("regression-replay-btn");
  await replay.click();
  await expect(page.getByTestId("run-regression-error")).toBeVisible();
  await expect.poll(() => requests.actionRequests().length).toBe(1);
  const ambiguousKey = (requests.actionRequests()[0].body.payload as Record<string, unknown>).idempotency_key;

  await replay.click();
  await expect(page.getByTestId("run-regression-error")).toContainText("replay rejected by gate");
  await expect.poll(() => requests.actionRequests().length).toBe(2);
  expect((requests.actionRequests()[1].body.payload as Record<string, unknown>).idempotency_key).toBe(ambiguousKey);

  await replay.click();
  await expect(page.getByTestId("regression-verdict")).toHaveText("Pass");
  await expect(replay).toBeDisabled();
  await expect(replay).toHaveText("Replayed");
  await expect.poll(() => requests.actionRequests().length).toBe(3);
  expect((requests.actionRequests()[2].body.payload as Record<string, unknown>).idempotency_key).not.toBe(ambiguousKey);
});

test("Runs keeps capture and replay pending results non-terminal with stable keys", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 960 });
  const requests = await installFixture(page, "light", undefined, {
    capturePendingOnce: true,
    replayPendingOnce: true,
  });
  await page.goto("/?page=delivery-trace&feature=F-GOAL-EVAL");

  await page.getByTestId("rg-task-TASK-REPLAY").click();
  const replay = page.getByTestId("regression-replay-btn");
  await replay.click();
  await expect(page.getByTestId("run-regression-pending")).toContainText("Replay is still processing");
  await expect(page.getByTestId("regression-verdict")).toHaveCount(0);
  await expect(replay).toBeEnabled();
  const replayPendingKey = (requests.actionRequests()[0].body.payload as Record<string, unknown>).idempotency_key;
  await replay.click();
  await expect(page.getByTestId("regression-verdict")).toHaveText("Pass");
  expect((requests.actionRequests()[1].body.payload as Record<string, unknown>).idempotency_key).toBe(replayPendingKey);
  await page.getByRole("button", { name: "close lifecycle drawer" }).click();

  await page.getByTestId("rg-task-TASK-AUTH").click();
  const capture = page.getByTestId("run-capture-regression");
  await capture.click();
  await expect(page.getByTestId("run-regression-pending")).toContainText("Capture is still processing");
  await expect(capture).not.toHaveText("Captured");
  await expect(capture).toBeEnabled();
  const capturePendingKey = (requests.actionRequests()[2].body.payload as Record<string, unknown>).idempotency_key;
  await capture.click();
  await expect(capture).toHaveText("Captured");
  await expect(capture).toBeDisabled();
  expect((requests.actionRequests()[3].body.payload as Record<string, unknown>).idempotency_key).toBe(capturePendingKey);
});

test("Runs preserves canonical status when bounded lifecycle details omit tasks", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 960 });
  await installFixture(page, "light", async (route, body) => {
    const bounded = structuredClone(body);
    const taskIds = Array.from({ length: 40 }, (_, index) => `TASK-${index + 1}`);
    const visibleTaskIds = taskIds.slice(0, 16);
    const statusTaskIds = taskIds.slice(0, 32);
    const taskStatuses = Object.fromEntries(statusTaskIds.map((taskId, index) => [
      taskId,
      index === 15 || index >= 28 ? "failed" : index === 14 ? "in_progress" : "done",
    ]));
    const lifecycleTasks = Object.fromEntries(taskIds.slice(0, 8).map((taskId, index) => [taskId, {
      state_history: [{ state: "done", entered_at: "2026-07-22T04:20:02Z", try: 1 }],
      state_history_total: index === 0 ? 4 : 1,
      state_history_included: 1,
      state_history_truncated: index === 0,
      tries: [{ try: 1, outcome: "done", dispatch_id: `dispatch-${taskId}`, gate_results: [] }],
      tries_total: index === 0 ? 3 : 1,
      tries_included: 1,
      tries_truncated: index === 0,
      gate_results_total: index === 0 ? 2 : 0,
      gate_results_included: 0,
      gate_results_truncated: index === 0,
    }]));
    const chain = bounded.run_chain as { stages: Array<Record<string, unknown>> };
    bounded.run_chain = {
      ...chain,
      stage_count: 3,
      stages_total: 3,
      stages_included: chain.stages.length,
      stages_omitted: 2,
      stages_truncated: true,
      task_ids_total: taskIds.length,
      task_ids_included: visibleTaskIds.length,
      task_ids_omitted: taskIds.length - visibleTaskIds.length,
      task_ids_truncated: true,
      stages: chain.stages.map((stage) => ({
        ...stage,
        task_ids: visibleTaskIds,
        task_ids_total: taskIds.length,
        task_ids_included: visibleTaskIds.length,
        task_ids_omitted: taskIds.length - visibleTaskIds.length,
        task_ids_truncated: true,
      })),
    };
    bounded.task_lifecycle = {
      schema_version: "task-lifecycle.v2",
      task_count: 40,
      tasks_included: 8,
      tasks_omitted: 32,
      tasks_truncated: true,
      task_statuses: taskStatuses,
      task_status_count: 40,
      task_statuses_included: statusTaskIds.length,
      task_statuses_omitted: taskIds.length - statusTaskIds.length,
      task_statuses_truncated: true,
      tasks: lifecycleTasks,
    };
    await route.fulfill({ contentType: "application/json", body: JSON.stringify(bounded) });
  });
  await page.goto("/?page=delivery-trace&feature=F-GOAL-EVAL");

  await expect(page.getByTestId("run-lifecycle-truncation")).toContainText("32 task lifecycle detail(s) omitted");
  await expect(page.getByTestId("run-lifecycle-truncation")).toContainText("canonical status unavailable for 8 task(s)");
  await expect(page.getByTestId("run-chain-truncation"))
    .toHaveText("2 stages omitted; 24 task relations omitted by the bounded projection.");
  await expect(page.getByTestId("rg-task-TASK-40")).toHaveCount(0);
  await expect(page.getByTestId("rg-task-TASK-16")).toHaveClass(/rg-state-failed/);
  await expect(page.getByTestId("rg-task-TASK-15")).toHaveClass(/rg-state-running/);

  await page.getByTestId("rg-search").fill("TASK-40");
  await page.getByTestId("rg-search").press("Enter");
  await expect(page.getByTestId("rg-search-miss"))
    .toHaveText("not included in this bounded projection");

  await page.getByTestId("rg-task-TASK-16").click();
  await expect(page.getByTestId("lifecycle-drawer")).toContainText("failed");
  await expect(page.getByTestId("ld-details-omitted")).toContainText("canonical status is failed");
  await expect(page.getByTestId("lc-tab-contract")).toHaveCount(0);
  await expect(page.getByRole("button", { name: "copy trace id" })).toHaveCount(0);
  await page.getByRole("button", { name: "close lifecycle drawer" }).click();

  await page.getByTestId("rg-task-TASK-1").click();
  await expect(page.getByTestId("ld-details-truncated")).toContainText("history showing 1 of 4");
  await expect(page.getByTestId("ld-details-truncated")).toContainText("attempts showing 1 of 3");
  await expect(page.getByTestId("ld-details-truncated")).toContainText("gate results showing 0 of 2");
});

test("Runs reports status-only truncation and does not infer healthy canonical state", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 960 });
  await installFixture(page, "light", async (route, body) => {
    const bounded = structuredClone(body);
    const lifecycle = bounded.task_lifecycle as Record<string, unknown>;
    const lifecycleTasks = structuredClone(lifecycle.tasks) as Record<string, Record<string, unknown>>;
    lifecycleTasks["TASK-REPLAY"] = {
      ...lifecycleTasks["TASK-REPLAY"],
      state_history: [{ state: "done", entered_at: "2026-07-22T04:20:11Z", try: 1 }],
      tries: [{ try: 1, outcome: "done", dispatch_id: "dispatch-replay-1", gate_results: [] }],
    };
    bounded.task_lifecycle = {
      ...lifecycle,
      tasks: lifecycleTasks,
      tasks_truncated: false,
      task_statuses: { "TASK-AUTH": "done" },
      task_status_count: 2,
      task_statuses_included: 1,
      task_statuses_truncated: true,
    };
    bounded.execution_graph = {
      ...(bounded.execution_graph as Record<string, unknown>),
      nodes: [
        { task_id: "TASK-AUTH", actual: { status: "failed" } },
        { task_id: "TASK-REPLAY", actual: { status: "done" } },
      ],
    };
    await route.fulfill({ contentType: "application/json", body: JSON.stringify(bounded) });
  });
  await page.goto("/?page=delivery-trace&feature=F-GOAL-EVAL");

  await expect(page.getByTestId("run-lifecycle-truncation"))
    .toHaveText("canonical status unavailable for 1 task(s).");
  await expect(page.getByTestId("run-lifecycle-truncation")).not.toContainText("lifecycle detail");
  await expect(page.getByTestId("rg-task-TASK-AUTH")).toHaveClass(/rg-state-done/);
  await expect(page.getByTestId("rg-task-TASK-REPLAY")).toHaveClass(/rg-state-none/);

  await page.getByTestId("rg-task-TASK-REPLAY").click();
  await expect(page.getByTestId("lifecycle-drawer").locator(".ld-head .badge").first()).toHaveText("unknown");
  await expect(page.getByTestId("ld-details-omitted"))
    .toContainText("Canonical task status was omitted");
});

test("mobile Runs keeps the single Run surface inside the viewport", async ({ page }, testInfo) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await installFixture(page, "dark");
  await page.goto("/?page=delivery-trace");

  await expect(page.getByTestId("run-graph")).toBeVisible();
  await expect(page.getByRole("tablist", { name: "Runs views" })).toHaveCount(0);
  await expect(page.getByTestId("delivery-trace-tab")).toHaveCount(0);
  await expectNoPageOverflow(page);
  await page.screenshot({ path: testInfo.outputPath("delivery-runs-mobile.png"), fullPage: true });
});

test("Delivery preserves the selected feature across tabs and reload", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 960 });
  await installFixture(page, "light");
  await page.goto("/?page=delivery-graph&feature=F-GOAL-EVAL");

  await page.getByRole("button", { name: secondFeature.title, exact: true }).click();
  await expect(page).toHaveURL(/page=delivery-graph/);
  await expect(page).toHaveURL(/feature=F-SECOND/);
  await expect(page.getByTestId("goal-coverage-goal-node")).toContainText("Ship the second delivery feature");

  await page.getByTestId("dt-mode-tab-trace").click();
  await expect(page).toHaveURL(/page=delivery-trace/);
  await expect(page).toHaveURL(/feature=F-SECOND/);
  await expect(page.getByTestId("run-graph")).toBeVisible();
  await page.reload();
  await expect(page.getByRole("heading", { name: "Runs", exact: true })).toBeVisible();
  await expect(page).toHaveURL(/feature=F-SECOND/);
  await expect(page.getByRole("button", { name: secondFeature.title, exact: true })).toHaveClass(/active/);

  await page.getByTestId("dt-mode-tab-overview").click();
  await expect(page).toHaveURL(/page=delivery/);
  await expect(page).toHaveURL(/feature=F-SECOND/);
  await expect(page.getByRole("heading", { name: "Delivery", exact: true })).toBeVisible();
  await page.getByTestId("dt-mode-tab-graph").click();
  await expect(page).toHaveURL(/page=delivery-graph/);
  await expect(page).toHaveURL(/feature=F-SECOND/);
  await expect(page.getByTestId("goal-coverage-goal-node")).toContainText("Ship the second delivery feature");
});

test("Delivery clears the previous view while a view-scoped request is deferred", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 960 });
  let releaseRuns!: () => void;
  const runsGate = new Promise<void>((resolve) => { releaseRuns = resolve; });
  await installFixture(page, "light", async (route, body) => {
    if (new URL(route.request().url()).searchParams.get("view") === "runs") await runsGate;
    await route.fulfill({ contentType: "application/json", body: JSON.stringify(body) });
  });
  await page.goto("/?page=delivery-graph&feature=F-GOAL-EVAL");
  await expect(page.getByTestId("goal-coverage-goal-node")).toContainText("Ship deterministic authentication");

  await page.getByTestId("dt-mode-tab-trace").click();
  await expect(page.getByRole("heading", { name: "Runs", exact: true })).toBeVisible();
  await expect(page.getByTestId("goal-coverage-page")).toHaveCount(0);
  releaseRuns();
  await expect(page.getByTestId("run-graph")).toBeVisible();
});

test("Delivery does not retain the previous feature after a failed switch", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 960 });
  let releaseFailure!: () => void;
  const failureGate = new Promise<void>((resolve) => { releaseFailure = resolve; });
  await installFixture(page, "light", async (route, body) => {
    const requestedFeatureId = decodeURIComponent(new URL(route.request().url()).pathname.split("/").at(-1) ?? "");
    if (requestedFeatureId !== secondFeature.id) {
      await route.fulfill({ contentType: "application/json", body: JSON.stringify(body) });
      return;
    }
    await failureGate;
    await route.fulfill({ status: 500, contentType: "application/json", body: JSON.stringify({ reason: "feature projection failed" }) });
  });
  await page.goto("/?page=delivery-graph&feature=F-GOAL-EVAL");
  await expect(page.getByTestId("goal-coverage-goal-node")).toContainText("Ship deterministic authentication");

  await page.getByRole("button", { name: secondFeature.title, exact: true }).click();
  await expect(page).toHaveURL(/feature=F-SECOND/);
  await expect(page.getByTestId("goal-coverage-page")).toHaveCount(0);
  releaseFailure();
  await expect(page.getByTestId("dt-error")).toContainText("returned 500");
  await expect(page.getByText("Ship deterministic authentication")).toHaveCount(0);
});

test("Graph reports bounded task details without inventing missing ownership", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 960 });
  await installFixture(page, "light", async (route, body) => {
    const bounded = structuredClone(body);
    const goalGraph = bounded.goal_coverage_graph as typeof graph;
    const retainedTaskIds = ["TASK-AUTH", ...Array.from({ length: 15 }, (_, index) => `TASK-RETAINED-${index + 1}`)];
    bounded.goal_coverage_graph = {
      ...goalGraph,
      nodes_truncated: true,
      edges_truncated: true,
      diagnostics_truncated: false,
      nodes: [
        ...goalGraph.nodes
          .filter((node) => node.task_id !== "TASK-REPLAY")
          .map((node) => node.goal_claim_id === "CLAIM-REPLAY"
            ? {
                ...node,
                task_ids: retainedTaskIds,
                task_details: {
                  total: 40,
                  included: 16,
                  missing_count: 24,
                  task_ids_returned: 16,
                  task_ids_truncated: true,
                },
              }
            : node),
        ...retainedTaskIds.slice(1).map((taskId) => ({
          node_id: `task:${taskId}`,
          kind: "task",
          task_id: taskId,
          title: `Retained task ${taskId}`,
          status: "in_progress",
          owner: "dev-core",
          goal_claim_ids: ["CLAIM-REPLAY"],
        })),
      ],
    };
    await route.fulfill({ contentType: "application/json", body: JSON.stringify(bounded) });
  });
  await page.goto("/?page=delivery-graph&feature=F-GOAL-EVAL");

  await expect(page.getByTestId("goal-coverage-truncation-notice")).toContainText("task owner details may be unavailable");
  const replayClaim = page.locator('[data-claim-id="CLAIM-REPLAY"]');
  await expect(replayClaim).toContainText("Owner details unavailable (24 omitted)");
  await expect(replayClaim).toContainText("1/40 done · details partial");
  await replayClaim.getByRole("button").click();
  await expect(page.getByTestId("goal-coverage-task-details-truncated")).toContainText("24 task owner details omitted");
  await expect(page.getByTestId("goal-coverage-inspector")).not.toContainText("No covering task");
});

test("Graph reports relation refs as bounded without understating open gaps", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 960 });
  await installFixture(page, "light", async (route, body) => {
    const bounded = structuredClone(body);
    const goalGraph = bounded.goal_coverage_graph as typeof graph;
    bounded.goal_coverage_graph = {
      ...goalGraph,
      nodes: goalGraph.nodes.map((node) => node.goal_claim_id === "CLAIM-REPLAY"
        ? {
            ...node,
            gap_refs: Array.from({ length: 4 }, (_, index) => `artifact://gap-${index + 1}`),
            gap_refs_total: 10,
            gap_refs_included: 4,
            gap_refs_omitted: 6,
            gap_refs_truncated: true,
          }
        : node),
    };
    await route.fulfill({ contentType: "application/json", body: JSON.stringify(bounded) });
  });
  await page.goto("/?page=delivery-graph&feature=F-GOAL-EVAL");

  await expect(page.getByTestId("goal-coverage-truncation-notice"))
    .toContainText("some relation refs are shown partially");
  const replayClaim = page.locator('[data-claim-id="CLAIM-REPLAY"]');
  await expect(replayClaim).toContainText("10 gaps · 4 shown");
  await replayClaim.getByRole("button").click();
  await expect(page.getByTestId("goal-coverage-gap-refs-truncated"))
    .toContainText("6 gap refs omitted");
});

test("legacy goal coverage deep link canonicalizes to the selected Delivery Graph", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 960 });
  await installFixture(page, "light");
  await page.goto("/?page=goal-coverage&feature=F-GOAL-EVAL");

  await expect(page).toHaveURL(/page=delivery-graph/);
  await expect(page).toHaveURL(/feature=F-GOAL-EVAL/);
  await expect(page.getByRole("heading", { name: "Graph", exact: true })).toBeVisible();
  await expect(page.getByTestId("goal-coverage-goal-node")).toContainText("F-GOAL-EVAL");
});

test("mobile Graph keeps Coverage and the lazy Work tree inside the viewport", async ({ page }, testInfo) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await installFixture(page, "dark");
  await page.goto("/?page=delivery-graph");

  await expect(page.getByTestId("delivery-map")).toBeVisible();
  await expect(page.getByTestId("goal-coverage-claim-row")).toHaveCount(3);
  await expect(page.getByRole("tablist", { name: "Graph view" })).toBeVisible();
  await expectNoPageOverflow(page);
  await page.getByRole("tab", { name: "Work" }).click();
  await expect(page.getByTestId("delivery-map-work")).toBeVisible();
  await expectNoPageOverflow(page);
  await page.screenshot({ path: testInfo.outputPath("delivery-graph-mobile.png"), fullPage: true });
});
