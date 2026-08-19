export type ContributionSectionKey = "findings" | "risks" | "contradictions" | "questions";

export interface ContributionRow {
  id: string;
  label: string;
  text: string;
}

export interface ContributionSection {
  key: ContributionSectionKey;
  title: string;
  visibleRows: ContributionRow[];
}

export interface ContributionReferencePresentation {
  label: string;
  total: number;
  refs: Record<string, unknown>;
}

export interface ContributionPresentation {
  sections: ContributionSection[];
}

const SECTION_CONFIG: Array<{
  key: ContributionSectionKey;
  title: string;
}> = [
  { key: "risks", title: "Risk" },
  { key: "contradictions", title: "Conflict" },
];

const GENERIC_LABELS = new Set([
  "contradiction",
  "fact",
  "finding",
  "question",
  "risk",
  "scope",
]);

const RISK_RANK: Record<string, number> = {
  blocker: 0,
  critical: 0,
  p0: 0,
  high: 1,
  p1: 1,
  medium: 2,
  p2: 2,
  low: 3,
  p3: 3,
};

export function presentContribution(payload: Record<string, unknown>): ContributionPresentation {
  const sections = SECTION_CONFIG.flatMap((config) => {
    const rows = semanticContributionRows(payload[config.key]);
    if (!rows.length) return [];
    const ordered = config.key === "risks"
      ? orderRisks(rows).filter(isActionableRisk)
      : rows;
    if (!ordered.length) return [];
    return [{
      key: config.key,
      title: config.title,
      visibleRows: ordered.slice(0, 1),
    } satisfies ContributionSection];
  });
  // The natural-language reply owns summary/findings. Typed data is only
  // allowed to add an actionable exception, never a second report.
  return { sections };
}

export function contributionReferencePresentation(
  payload: Record<string, unknown>,
  refs: Record<string, unknown> = {},
): ContributionReferencePresentation {
  const sourceRefs = uniqueStrings(payload.source_refs ?? refs.source_refs);
  const evidenceRefs = uniqueStrings(payload.evidence_refs ?? refs.evidence_refs);
  const artifactRef = textValue(payload.artifact_ref ?? refs.artifact_ref);
  const artifactDigest = textValue(payload.artifact_digest ?? refs.artifact_digest);
  const artifactCount = artifactRef ? 1 : 0;
  const parts = [
    countLabel(sourceRefs.length, "source"),
    countLabel(evidenceRefs.length, "evidence ref"),
    countLabel(artifactCount, "artifact"),
  ].filter(Boolean);
  const previewRefs: Record<string, unknown> = {};
  if (sourceRefs.length) previewRefs.source_refs = sourceRefs;
  if (evidenceRefs.length) previewRefs.evidence_refs = evidenceRefs;
  if (artifactRef) {
    previewRefs.artifacts = [{
      kind: "artifact",
      path: artifactRef,
      ...(artifactDigest ? { sha256: artifactDigest } : {}),
    }];
  }
  return {
    label: parts.join(" · "),
    total: sourceRefs.length + evidenceRefs.length + artifactCount,
    refs: previewRefs,
  };
}

export function contributionRowLabel(
  section: ContributionSectionKey,
  label: string,
): string {
  const normalized = label.trim().toLowerCase();
  if (!normalized || GENERIC_LABELS.has(normalized)) return "";
  if (section === "risks" && normalized in RISK_RANK) return normalized.toUpperCase();
  return label.trim();
}

export function semanticContributionRows(value: unknown): ContributionRow[] {
  if (!Array.isArray(value)) return [];
  return value.flatMap((item) => {
    if (!item || typeof item !== "object" || Array.isArray(item)) return [];
    const row = item as Record<string, unknown>;
    const text = textValue(
      row.text
      ?? row.statement
      ?? row.risk
      ?? row.question
      ?? row.summary
      ?? row.description,
    );
    if (!text) return [];
    return [{
      id: textValue(row.id ?? row.question_id),
      label: textValue(row.priority ?? row.label ?? row.type ?? row.category),
      text,
    }];
  });
}

function orderRisks(rows: ContributionRow[]): ContributionRow[] {
  return rows
    .map((row, index) => ({ row, index }))
    .sort((left, right) => {
      const rank = riskRank(left.row.label) - riskRank(right.row.label);
      return rank || left.index - right.index;
    })
    .map(({ row }) => row);
}

function riskRank(label: string): number {
  return RISK_RANK[label.trim().toLowerCase()] ?? 4;
}

function isActionableRisk(row: ContributionRow): boolean {
  return riskRank(row.label) <= 1;
}

function uniqueStrings(value: unknown): string[] {
  if (!Array.isArray(value)) return [];
  return Array.from(new Set(value.map(textValue).filter(Boolean)));
}

function textValue(value: unknown): string {
  return value === null || value === undefined ? "" : String(value).trim();
}

function countLabel(count: number, noun: string): string {
  if (!count) return "";
  return `${count} ${noun}${count === 1 ? "" : "s"}`;
}
