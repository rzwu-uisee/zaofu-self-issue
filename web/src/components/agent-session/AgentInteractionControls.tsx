import { Check, MessageCircle, PencilLine, X } from "lucide-react";
import { useState } from "react";
import type {
  AgentSessionActionProposal,
  AgentSessionPlanAnswer,
  AgentSessionPlanOption,
  AgentSessionPlanQuestion,
  AgentSessionPlanRequest,
  AgentSessionPlanResponse,
} from "./types";
import { actionPresentation } from "./actionPresentation";

function displayPlanOptionLabel(label: string): string {
  return label
    .replace(/\s*\((?:Recommended|推荐)\)\s*$/i, "")
    .trim();
}

function planOptionDetails(option?: AgentSessionPlanOption): string {
  const details = option?.submitDetails;
  if (!details) return "";
  const workflowDetails = details.routeId ? [
    [details.family, details.topology]
      .filter(Boolean)
      .map((value) => value?.replaceAll("_", " "))
      .join(" / "),
    details.laneCount
      ? `${details.laneCount} lane${details.laneCount === 1 ? "" : "s"}`
      : "",
    (details.roles || []).map((role) => role.replaceAll("_", " ")).join(", "),
    details.outputProfile
      ? `Output: ${details.outputProfile.replaceAll("_", " ")}`
      : "",
  ] : [];
  const channelDetails = details.templateId ? [
    details.templateName || details.templateId,
    `${details.memberCount || 0} members`,
    (details.roles || []).map((role) => role.replaceAll("_", " ")).join(", "),
    `${details.maxRounds || 0} rounds`,
  ] : [];
  return [...workflowDetails, ...channelDetails].filter(Boolean).join(" | ");
}

function planQuestions(request: AgentSessionPlanRequest): AgentSessionPlanQuestion[] {
  return request.questions?.length ? request.questions : [{
    id: request.questionId,
    header: request.header,
    question: request.question,
    options: request.options,
    allowOther: request.allowOther,
  }];
}

function PlanSummary({
  request,
}: {
  request: AgentSessionPlanRequest;
}) {
  const questions = planQuestions(request);
  const answers = request.response?.answers?.length
    ? request.response.answers
    : request.response
      ? [{
        questionId: request.response.questionId,
        optionId: request.response.optionId,
        answer: request.response.answer,
      }]
      : [];
  const answerByQuestion = new Map(
    answers.map((answer) => [answer.questionId, answer]),
  );
  const selectedOption = answers.flatMap((answer) => (
    questions
      .find((question) => question.id === answer.questionId)
      ?.options.filter((option) => option.id === answer.optionId) || []
  )).find((option) => (
    option.submitMode === "apply" || option.submitMode === "propose"
  ));
  const detailText = planOptionDetails(selectedOption);
  const status = selectedOption?.submitMode === "propose"
    ? "Ready for confirmation"
    : selectedOption?.submitMode === "apply"
      ? "Plan applied"
      : "Plan complete";
  return (
    <div className="agent-plan-summary" title={request.question}>
      <div className="agent-plan-summary-title">
        <Check aria-hidden="true" size={15} />
        <strong>Plan summary</strong>
        <span>{status}</span>
      </div>
      <div className="agent-plan-summary-answers">
        {questions.map((question) => {
          const answer = answerByQuestion.get(question.id);
          if (!answer) return null;
          return (
            <div className="agent-plan-summary-answer" key={question.id}>
              <small>{question.question}</small>
              <strong>{answer.answer}</strong>
            </div>
          );
        })}
      </div>
      {detailText ? (
        <small className="agent-plan-option-details">{detailText}</small>
      ) : null}
    </div>
  );
}

export function PlanInteractionForm({
  busy = false,
  disabled = false,
  onChatAbout,
  onSubmit,
  request,
}: {
  busy?: boolean;
  disabled?: boolean;
  onChatAbout?: () => void;
  onSubmit?: (response: AgentSessionPlanResponse) => void;
  request: AgentSessionPlanRequest;
}) {
  const questions = planQuestions(request);
  const [selected, setSelected] = useState<Record<string, string>>({});
  const [otherAnswers, setOtherAnswers] = useState<Record<string, string>>({});
  if (request.response) {
    return <PlanSummary request={request} />;
  }

  const answers = questions.flatMap((question): AgentSessionPlanAnswer[] => {
    const optionId = selected[question.id] || "";
    if (!optionId) return [];
    const answer = optionId === "other"
      ? (otherAnswers[question.id] || "").trim()
      : question.options.find((option) => option.id === optionId)?.label || "";
    return answer ? [{ questionId: question.id, optionId, answer }] : [];
  });
  const canSubmit = Boolean(
    request.valid
    && answers.length === questions.length
    && onSubmit
    && !busy
    && !disabled,
  );
  const selectedOption = answers.flatMap((answer) => (
    questions
      .find((question) => question.id === answer.questionId)
      ?.options.filter((option) => option.id === answer.optionId) || []
  )).find((option) => (
    option.submitMode === "apply" || option.submitMode === "propose"
  ));
  const selectedMode = selectedOption?.submitMode || request.submitMode;
  const submitLabel = (
    selectedMode === "apply"
      ? request.submitLabel || "Apply"
      : "Continue"
  );
  return (
    <div aria-busy={busy} className="agent-plan-form">
      {questions.map((question, questionIndex) => (
        <fieldset
          className="agent-plan-question"
          disabled={busy || disabled || !request.valid}
          key={question.id}
        >
          <legend>
            {questions.length > 1 ? (
              <small>{questionIndex + 1} of {questions.length}</small>
            ) : null}
            {question.question}
          </legend>
          <div className="agent-plan-options">
            {question.options.map((option) => {
              const detailText = planOptionDetails(option);
              return (
                <label className="agent-plan-option" key={option.id}>
                  <input
                    aria-label={option.label}
                    checked={selected[question.id] === option.id}
                    name={`plan-${request.requestEventId}-${question.id}`}
                    type="radio"
                    value={option.id}
                    onChange={() => setSelected((current) => ({
                      ...current,
                      [question.id]: option.id,
                    }))}
                  />
                  <span>
                    <span className="agent-plan-option-heading">
                      <strong>{displayPlanOptionLabel(option.label)}</strong>
                      {option.recommended ? (
                        <small className="agent-plan-recommended">Recommended</small>
                      ) : null}
                    </span>
                    {option.description ? <small>{option.description}</small> : null}
                    {detailText ? <small className="agent-plan-option-details">{detailText}</small> : null}
                  </span>
                </label>
              );
            })}
            {question.allowOther ? (
              <label className="agent-plan-option other">
                <input
                  checked={selected[question.id] === "other"}
                  name={`plan-${request.requestEventId}-${question.id}`}
                  type="radio"
                  value="other"
                  onChange={() => setSelected((current) => ({
                    ...current,
                    [question.id]: "other",
                  }))}
                />
                <span>
                  <strong>Customize</strong>
                  {selected[question.id] === "other" ? (
                    <textarea
                      aria-label={`Custom answer for ${question.question}`}
                      autoFocus
                      rows={2}
                      value={otherAnswers[question.id] || ""}
                      onChange={(event) => setOtherAnswers((current) => ({
                        ...current,
                        [question.id]: event.target.value,
                      }))}
                    />
                  ) : null}
                </span>
              </label>
            ) : null}
          </div>
        </fieldset>
      ))}
      {request.validationError ? (
        <p className="agent-action-warning">{request.validationError}</p>
      ) : null}
      <div className="agent-plan-actions">
        <button
          className="agent-inline-button"
          disabled={busy || disabled || !onChatAbout}
          type="button"
          onClick={onChatAbout}
        >
          <MessageCircle aria-hidden="true" size={14} />
          Chat about
        </button>
        <button
          className="agent-inline-button primary"
          disabled={!canSubmit}
          type="button"
          onClick={() => {
            const primary = answers[0];
            if (!primary) return;
            onSubmit?.({
              requestEventId: request.requestEventId,
              requestId: request.requestId,
              revision: request.revision,
              questionId: primary.questionId,
              optionId: primary.optionId,
              answer: primary.answer,
              answers,
            });
          }}
        >
          <Check aria-hidden="true" size={14} />
          {busy ? (selectedMode === "apply" ? "Applying" : "Continuing") : submitLabel}
        </button>
      </div>
    </div>
  );
}

export function ApproveInteractionActions({
  busy = false,
  onApprove,
  onReject,
  onRevise,
  proposal,
}: {
  busy?: boolean;
  onApprove?: () => void;
  onReject?: () => void;
  onRevise?: () => void;
  proposal: AgentSessionActionProposal;
}) {
  const presentation = actionPresentation(proposal.action);
  return (
    <div className="agent-approve-actions">
      <button
        className="agent-inline-button"
        disabled={busy || !onRevise}
        type="button"
        onClick={onRevise}
      >
        <PencilLine aria-hidden="true" size={14} />
        Edit
      </button>
      <button
        className="agent-inline-button"
        disabled={busy || !onReject}
        type="button"
        onClick={onReject}
      >
        <X aria-hidden="true" size={14} />
        Cancel
      </button>
      <button
        className="agent-inline-button primary agent-action-primary"
        disabled={busy || !proposal.valid || !onApprove}
        type="button"
        onClick={onApprove}
      >
        <Check aria-hidden="true" size={14} />
        {busy ? presentation.busyLabel : presentation.confirmLabel}
      </button>
    </div>
  );
}
