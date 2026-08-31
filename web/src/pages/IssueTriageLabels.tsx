import type { CSSProperties, SyntheticEvent } from "react";
import { issueLabelFoldCount } from "./issueTriageModel";

function githubLabelStyle(name: string, colors?: Record<string, string>): CSSProperties | undefined {
  const color = String(colors?.[name] || "").replace(/^#/, "");
  if (!/^[0-9a-f]{6}$/i.test(color)) return undefined;
  const red = Number.parseInt(color.slice(0, 2), 16);
  const green = Number.parseInt(color.slice(2, 4), 16);
  const blue = Number.parseInt(color.slice(4, 6), 16);
  const luminance = (0.299 * red + 0.587 * green + 0.114 * blue) / 255;
  return {
    backgroundColor: `#${color}`,
    borderColor: `#${color}`,
    color: luminance > 0.62 ? "#172033" : "#ffffff",
  };
}

export function IssueLabelList({
  labels,
  colors,
  onSelect,
}: {
  labels: string[];
  colors?: Record<string, string>;
  onSelect: (label: string) => void;
}) {
  const foldedCount = issueLabelFoldCount(labels);
  const folded = foldedCount > 0;
  const stop = (event: SyntheticEvent) => event.stopPropagation();
  return (
    <span className={`issue-label-row ${folded ? "foldable" : ""}`} aria-label="GitHub labels">
      {labels.map((label, index) => (
        <button
          aria-label={`Filter issues by label ${label}`}
          className={`badge issue-label-badge issue-triage-tooltip ${index >= 3 ? "issue-label-overflow" : ""}`}
          data-tooltip={`Show only issues labelled “${label}”`}
          key={label}
          style={githubLabelStyle(label, colors)}
          type="button"
          onClick={(event) => { stop(event); onSelect(label); }}
          onKeyDown={stop}
        >
          {label}
        </button>
      ))}
      {folded ? (
        <button
          aria-label={`Show ${foldedCount} more labels`}
          className="badge issue-label-more issue-triage-tooltip"
          data-tooltip="Hover or focus to show all labels"
          type="button"
          onClick={stop}
          onKeyDown={stop}
        >
          +{foldedCount}
        </button>
      ) : null}
    </span>
  );
}

export { githubLabelStyle };
