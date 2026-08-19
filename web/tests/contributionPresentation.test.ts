import {
  contributionReferencePresentation,
  contributionRowLabel,
  presentContribution,
} from "../src/components/agent-session/contributionPresentation.js";

function assert(condition: unknown, message: string): void {
  if (!condition) throw new Error(message);
}

const presentation = presentContribution({
  findings: [
    { id: "f1", type: "finding", text: "One authority owns state." },
    { id: "f2", type: "fact", statement: "Events preserve occurrence order." },
    { id: "f3", text: "Artifacts retain semantic bodies." },
    { id: "f4", text: "Web remains a read-oriented projection." },
  ],
  risks: [
    { id: "r2", priority: "p2", risk: "Low-priority display drift." },
    { id: "r0", priority: "p0", risk: "Dual writes make replay diverge." },
    { id: "r1", priority: "p1", risk: "A stale projection hides owner input." },
  ],
  contradictions: [
    { id: "c1", text: "Two documents claim canonical authority." },
    { id: "c2", text: "The UI names a phase the runtime does not expose." },
  ],
  questions: [
    { question_id: "q1", category: "scope", question: "Which vertical slice ships first?" },
    { question_id: "q2", question: "Who owns the verification decision?" },
  ],
});

assert(presentation.sections.length === 2, "only actionable risk and conflict sections enter chat");
assert(!presentation.sections.some((section) => section.key === "findings"), "findings stay in the typed projection, not chat");
assert(!presentation.sections.some((section) => section.key === "questions"), "questions are owned by AskUserQuestion");
const risks = presentation.sections.find((section) => section.key === "risks");
assert(risks?.visibleRows[0]?.id === "r0", "P0 risk is promoted ahead of lower priorities");
assert(risks?.visibleRows.length === 1, "only the highest risk is shown inline");
const conflicts = presentation.sections.find((section) => section.key === "contradictions");
assert(conflicts?.visibleRows[0]?.id === "c1", "the first unresolved conflict stays actionable");
assert(contributionRowLabel("findings", "fact") === "", "generic FACT labels do not add visual noise");
assert(contributionRowLabel("questions", "scope") === "", "generic scope labels stay out of the transcript");
assert(contributionRowLabel("risks", "p0") === "P0", "risk priority remains explicit");

const fallback = presentContribution({});
assert(fallback.sections.length === 0, "empty semantic payload has no invented sections");

const references = contributionReferencePresentation({
  source_refs: ["SPEC.md", "SPEC.md", "tasks/plan.md"],
  evidence_refs: ["trace:1", "test:2"],
  artifact_ref: "channels/ch-test/contracts/contribution/reply.json",
  artifact_digest: "abc123",
});
assert(references.total === 5, "reference total deduplicates sources and includes the artifact");
assert(
  references.label === "2 sources · 2 evidence refs · 1 artifact",
  "reference summary stays compact and readable",
);
assert(Array.isArray(references.refs.source_refs), "source refs remain available to the preview registry");
assert(Array.isArray(references.refs.evidence_refs), "evidence refs remain available to the preview registry");
assert(Array.isArray(references.refs.artifacts), "artifact ref becomes an inspectable preview item");

console.log("contributionPresentation.test.ts OK");
