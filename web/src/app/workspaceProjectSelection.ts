import type { WorkspaceProject } from "../api/types";

export function resolveWorkspaceProjectId(
  currentProjectId: string,
  serverActiveProjectId: string,
  projects: WorkspaceProject[],
): string {
  const registeredIds = new Set(projects.map((project) => project.project_id));
  if (registeredIds.has(currentProjectId)) return currentProjectId;
  if (registeredIds.has(serverActiveProjectId)) return serverActiveProjectId;
  return projects[0]?.project_id ?? "";
}
