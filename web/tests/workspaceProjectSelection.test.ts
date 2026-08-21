import { resolveWorkspaceProjectId } from "../src/app/workspaceProjectSelection.js";
import type { WorkspaceProject } from "../src/api/types.js";

function assertEqual(actual: unknown, expected: unknown, message: string): void {
  if (actual !== expected) {
    throw new Error(`${message}: expected ${String(expected)}, got ${String(actual)}`);
  }
}

function project(projectId: string): WorkspaceProject {
  return {
    project_id: projectId,
    name: projectId,
    root: `/workspace/${projectId}`,
    config_path: `/workspace/${projectId}/zf.yaml`,
    state_dir_hint: `/workspace/${projectId}/.zf`,
  };
}

const projects = [project("project-a"), project("project-b")];

assertEqual(
  resolveWorkspaceProjectId("project-b", "project-a", projects),
  "project-b",
  "a registered browser selection should remain active",
);
assertEqual(
  resolveWorkspaceProjectId("removed-project", "project-a", projects),
  "project-a",
  "a stale browser selection should recover to the registered server active project",
);
assertEqual(
  resolveWorkspaceProjectId("removed-project", "removed-server-project", projects),
  "project-a",
  "a stale server pointer should fall back to the first registered project",
);
assertEqual(
  resolveWorkspaceProjectId("removed-project", "removed-server-project", []),
  "",
  "an empty registry should clear a stale browser selection",
);

console.log("workspaceProjectSelection tests passed");
