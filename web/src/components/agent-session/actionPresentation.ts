export interface ActionPresentation {
  title: string;
  confirmLabel: string;
  busyLabel: string;
  completedLabel: string;
}

const ACTION_PRESENTATIONS: Record<string, ActionPresentation> = {
  "apply-patch-proposal": {
    title: "Apply code changes",
    confirmLabel: "Apply changes",
    busyLabel: "Applying",
    completedLabel: "Changes applied",
  },
  "channel-create-from-template": {
    title: "Create channel",
    confirmLabel: "Create channel",
    busyLabel: "Creating",
    completedLabel: "Channel created",
  },
  "channel-create-and-start": {
    title: "Create and start channel",
    confirmLabel: "Create & start",
    busyLabel: "Creating",
    completedLabel: "Channel started",
  },
  "channel-discussion-start": {
    title: "Start discussion",
    confirmLabel: "Start discussion",
    busyLabel: "Starting",
    completedLabel: "Discussion started",
  },
  "create-task": {
    title: "Create task",
    confirmLabel: "Create task",
    busyLabel: "Creating",
    completedLabel: "Task created",
  },
  "idea-to-product": {
    title: "Start product workflow",
    confirmLabel: "Start workflow",
    busyLabel: "Starting",
    completedLabel: "Product workflow started",
  },
  "research-adopt": {
    title: "Adopt research",
    confirmLabel: "Adopt research",
    busyLabel: "Adopting",
    completedLabel: "Research adopted",
  },
  "research-start": {
    title: "Start research",
    confirmLabel: "Start research",
    busyLabel: "Starting",
    completedLabel: "Research started",
  },
  "workflow-start": {
    title: "Start workflow",
    confirmLabel: "Start workflow",
    busyLabel: "Starting",
    completedLabel: "Workflow started",
  },
  "task-workflow-start": {
    title: "Start workflow",
    confirmLabel: "Start workflow",
    busyLabel: "Starting",
    completedLabel: "Workflow started",
  },
  "update-task": {
    title: "Update task",
    confirmLabel: "Update task",
    busyLabel: "Updating",
    completedLabel: "Task updated",
  },
  "workflow-invoke": {
    title: "Start workflow",
    confirmLabel: "Start workflow",
    busyLabel: "Starting",
    completedLabel: "Workflow started",
  },
};

function sentenceCaseAction(action: string): string {
  const words = action.trim().replace(/[_-]+/g, " ");
  return words ? `${words.charAt(0).toUpperCase()}${words.slice(1)}` : "Run action";
}

export function actionPresentation(action: string): ActionPresentation {
  const known = ACTION_PRESENTATIONS[action];
  if (known) return known;
  const title = sentenceCaseAction(action);
  return {
    title,
    confirmLabel: title,
    busyLabel: "Running",
    completedLabel: `${title} completed`,
  };
}
