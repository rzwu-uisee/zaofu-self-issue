import {
  Check,
  CircleStop,
  ExternalLink,
  FileDiff,
  GitFork,
  Pause,
  Play,
  RefreshCw,
  ShieldCheck,
  X,
} from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";

import {
  getWorkflowRequestDetail,
  getWorkflowRequests,
} from "../../api/client";
import type {
  ActionResponse,
  WorkflowRequestDetail,
} from "../../api/types";
import type { PageId } from "../../app/sharedTypes";
import {
  asRecord,
  asRecordArray,
  asStringArray,
  stringify,
  textValue,
} from "../../app/shared";

type WorkflowRequest = Record<string, unknown>;

export function WorkflowProposalPage({
  actionReady,
  actionState,
  onAction,
  onOpenPage,
  projectId,
}: {
  actionReady: boolean;
  actionState: string;
  onAction: (
    action: string,
    payload: Record<string, unknown>,
  ) => Promise<ActionResponse>;
  onOpenPage: (page: PageId) => void;
  projectId: string;
}) {
  const [requests, setRequests] = useState<WorkflowRequest[]>([]);
  const [selectedId, setSelectedId] = useState("");
  const [detail, setDetail] = useState<WorkflowRequestDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [busyAction, setBusyAction] = useState("");
  const [feedback, setFeedback] = useState<ActionResponse | null>(null);

  const loadRequests = useCallback(async () => {
    if (!projectId) {
      setRequests([]);
      setSelectedId("");
      setLoading(false);
      return;
    }
    try {
      const page = await getWorkflowRequests(projectId);
      setRequests(page.items);
      setSelectedId((current) => {
        if (page.items.some((item) => textValue(item.request_id) === current)) {
          return current;
        }
        return textValue(page.items[0]?.request_id);
      });
      setError("");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setLoading(false);
    }
  }, [projectId]);

  const loadDetail = useCallback(async () => {
    if (!projectId || !selectedId) {
      setDetail(null);
      return;
    }
    try {
      const next = await getWorkflowRequestDetail(selectedId, projectId);
      setDetail(next);
      setError("");
    } catch (reason) {
      setDetail(null);
      setError(reason instanceof Error ? reason.message : String(reason));
    }
  }, [projectId, selectedId]);

  useEffect(() => {
    setLoading(true);
    setDetail(null);
    void loadRequests();
    const timer = window.setInterval(() => void loadRequests(), 8000);
    return () => window.clearInterval(timer);
  }, [loadRequests]);

  useEffect(() => {
    void loadDetail();
  }, [loadDetail]);

  const operationStatus = textValue(detail?.operation?.queue_status);
  useEffect(() => {
    if (!["queued", "running"].includes(operationStatus)) return undefined;
    const timer = window.setInterval(() => {
      void loadDetail();
      void loadRequests();
    }, 1500);
    return () => window.clearInterval(timer);
  }, [loadDetail, loadRequests, operationStatus]);

  async function execute(
    action:
      | "workflow-submit"
      | "workflow-reject"
      | "workflow-config-apply"
      | "workflow-cancel"
      | "run-pause"
      | "run-resume"
      | "run-cancel",
    payload: Record<string, unknown>,
  ) {
    if (busyAction) return;
    setBusyAction(action);
    setFeedback(null);
    try {
      const result = await onAction(action, payload);
      setFeedback(result);
      await loadRequests();
      await loadDetail();
    } catch (reason) {
      setFeedback({
        ok: false,
        status: "failed",
        action,
        reason: reason instanceof Error ? reason.message : String(reason),
      });
    } finally {
      setBusyAction("");
    }
  }

  const statusCounts = useMemo(() => {
    const counts: Record<string, number> = {};
    for (const request of requests) {
      const status = textValue(request.status) || "unknown";
      counts[status] = (counts[status] ?? 0) + 1;
    }
    return counts;
  }, [requests]);

  return (
    <div className="workflow-proposal-page" data-testid="workflow-proposal-page">
      <header className="workflow-proposal-toolbar">
        <div>
          <h2>Workflow Proposals</h2>
          <span className="muted">
            {requests.length} requests
            {statusCounts.proposed ? ` / ${statusCounts.proposed} awaiting decision` : ""}
          </span>
        </div>
        <button
          aria-label="Refresh workflow proposals"
          className="icon-button"
          title="Refresh"
          type="button"
          onClick={() => {
            void loadRequests();
            void loadDetail();
          }}
        >
          <RefreshCw aria-hidden="true" size={16} />
        </button>
      </header>

      {error ? <div className="workflow-proposal-notice tone-error">{error}</div> : null}
      {feedback ? (
        <div
          className={`workflow-proposal-notice ${feedback.ok ? "tone-ok" : "tone-error"}`}
          data-testid="workflow-proposal-feedback"
        >
          <strong>{feedback.status}</strong>
          <span>{feedback.reason || decisionFeedback(feedback)}</span>
        </div>
      ) : null}

      {loading ? (
        <WorkflowEmpty title="Loading workflow proposals" />
      ) : requests.length === 0 ? (
        <WorkflowEmpty title="No workflow proposals" />
      ) : (
        <div className="workflow-proposal-workbench">
          <aside className="workflow-request-list" aria-label="Workflow requests">
            {requests.map((request) => {
              const requestId = textValue(request.request_id);
              const synthesisStatus = textValue(asRecord(request.operation).queue_status);
              return (
                <button
                  className={`workflow-request-row ${requestId === selectedId ? "active" : ""}`}
                  key={requestId}
                  type="button"
                  onClick={() => {
                    setFeedback(null);
                    setSelectedId(requestId);
                  }}
                >
                  <span className="workflow-request-row-head">
                    <strong>{requestTitle(request)}</strong>
                    <StatusBadge status={textValue(request.status)} />
                  </span>
                  <span className="mono">{requestId}</span>
                  <small>
                    {textValue(request.kind) || "flow"} / revision {textValue(request.revision) || "1"}
                    {synthesisStatus ? ` / synthesis ${synthesisStatus}` : ""}
                  </small>
                </button>
              );
            })}
          </aside>

          <main className="workflow-proposal-detail">
            {detail ? (
              <WorkflowProposalDetail
                actionReady={actionReady}
                actionState={actionState}
                busyAction={busyAction}
                detail={detail}
                onApply={(payload) => void execute("workflow-config-apply", payload)}
                onApprove={(payload) => void execute("workflow-submit", payload)}
                onCancel={(payload) => void execute("workflow-cancel", payload)}
                onOpenRuns={() => onOpenPage("runs")}
                onReject={(payload) => void execute("workflow-reject", payload)}
                onRunCancel={(payload) => void execute("run-cancel", payload)}
                onRunPause={(payload) => void execute("run-pause", payload)}
                onRunResume={(payload) => void execute("run-resume", payload)}
              />
            ) : (
              <WorkflowEmpty title="Loading proposal detail" />
            )}
          </main>
        </div>
      )}
    </div>
  );
}

function WorkflowProposalDetail({
  actionReady,
  actionState,
  busyAction,
  detail,
  onApply,
  onApprove,
  onCancel,
  onOpenRuns,
  onReject,
  onRunCancel,
  onRunPause,
  onRunResume,
}: {
  actionReady: boolean;
  actionState: string;
  busyAction: string;
  detail: WorkflowRequestDetail;
  onApply: (payload: Record<string, unknown>) => void;
  onApprove: (payload: Record<string, unknown>) => void;
  onCancel: (payload: Record<string, unknown>) => void;
  onOpenRuns: () => void;
  onReject: (payload: Record<string, unknown>) => void;
  onRunCancel: (payload: Record<string, unknown>) => void;
  onRunPause: (payload: Record<string, unknown>) => void;
  onRunResume: (payload: Record<string, unknown>) => void;
}) {
  const request = detail.result;
  const requirement = detail.requirement;
  const proposal = detail.proposal;
  const operation = detail.operation;
  const artifacts = detail.artifacts;
  const lifecycle = detail.lifecycle;
  const links = detail.links;
  const proposalRef = asRecord(request.proposal_ref);
  const proposalDigest = textValue(request.proposal_digest);
  const requestId = textValue(request.request_id);
  const requestStatus = textValue(request.status);
  const changeMode = textValue(proposal.change_mode);
  const configApplied = lifecycle.config_applied === true;
  const submitted = lifecycle.submitted === true || ["submitted", "running"].includes(requestStatus);
  const terminal = textValue(lifecycle.terminal);
  const runAdmission = asRecord(lifecycle.admission);
  const runStatus = textValue(runAdmission.status);
  const runId = textValue(runAdmission.run_id) || textValue(links.run_id);
  const approvable = (
    requestStatus === "proposed"
    && textValue(proposal.approval_status) === "approvable"
    && Boolean(proposalDigest)
    && Boolean(proposalRef.ref)
  );
  const decisionPayload = {
    request_id: requestId,
    proposal_ref: proposalRef,
    proposal_digest: proposalDigest,
  };
  const submitPayload = {
    ...decisionPayload,
    intake_ref: textValue(links.intake_ref),
    kind: textValue(request.kind),
    allow_missing_env: true,
  };
  const validationResultRef = asRecord(proposal.validation_result_ref);
  const configApplyPayload = {
    ...decisionPayload,
    proposal_id: textValue(proposal.proposal_id),
    validation_result_ref: validationResultRef,
    approval_ref: `web:workflow-proposal:${requestId}:${proposalDigest}`,
    idempotency_key: `workflow-config-apply:${proposalDigest}`,
  };
  const stages = asRecordArray(asRecord(proposal.stage_graph).nodes);
  const blockers = asRecordArray(proposal.blockers);
  const preflight = asRecord(proposal.preflight);
  const warnings = asRecordArray(preflight.diagnostics).filter(
    (item) => textValue(item.severity).toUpperCase() !== "STOP",
  );
  const closure = asRecord(proposal.closure);
  const roles = asRecordArray(closure.roles);
  const profiles = Object.entries(asRecord(closure.execution_profiles));
  const configDiff = asRecord(artifacts.config_diff);
  const shortFlowSpec = asRecord(artifacts.short_flow_spec);
  const completionProfile = asRecord(proposal.completion_profile);
  const estimated = asRecord(proposal.estimated);
  const synthesisStatus = textValue(operation.queue_status);
  const synthesisCancellable = ["queued", "running"].includes(synthesisStatus);
  const cancelPayload = {
    request_id: requestId,
    operation_id: textValue(operation.operation_id),
    request_hash: (
      textValue(operation.request_hash)
      || textValue(request.synthesis_request_hash)
    ),
    reason: "cancelled by operator",
  };
  const runControlPayload = {
    run_id: runId,
    request_id: requestId,
    reason: "requested by workflow proposal operator",
  };

  return (
    <>
      <header className="workflow-proposal-detail-head">
        <div>
          <span className="eyebrow">Requirement revision {textValue(request.revision)}</span>
          <h3>{textValue(requirement.objective) || requestId}</h3>
          <div className="workflow-proposal-meta">
            <StatusBadge status={requestStatus} />
            <span>{textValue(proposal.flow_family) || textValue(request.kind)}</span>
            <span>{textValue(proposal.risk_class) || "not classified"}</span>
            <code title={proposalDigest}>{shortDigest(proposalDigest)}</code>
            {synthesisStatus ? (
              <StatusBadge status={`synthesis ${synthesisStatus}`} />
            ) : null}
          </div>
        </div>
        <div className="workflow-proposal-actions">
          {runStatus === "running" ? (
            <button
              className="icon-button"
              disabled={!actionReady || busyAction !== "" || !runId}
              title={!actionReady ? actionState : "Pause new dispatch for this Run"}
              type="button"
              onClick={() => onRunPause(runControlPayload)}
            >
              <Pause aria-hidden="true" size={16} />
              {busyAction === "run-pause" ? "Pausing" : "Pause Run"}
            </button>
          ) : null}
          {runStatus === "paused" ? (
            <button
              className="icon-button"
              disabled={!actionReady || busyAction !== "" || !runId}
              title={!actionReady ? actionState : "Resume this Run"}
              type="button"
              onClick={() => onRunResume(runControlPayload)}
            >
              <Play aria-hidden="true" size={16} />
              {busyAction === "run-resume" ? "Resuming" : "Resume Run"}
            </button>
          ) : null}
          {["queued", "running", "paused"].includes(runStatus) ? (
            <button
              className="icon-button danger"
              disabled={!actionReady || busyAction !== "" || !runId}
              title={!actionReady ? actionState : "Cancel this Run"}
              type="button"
              onClick={() => onRunCancel(runControlPayload)}
            >
              <CircleStop aria-hidden="true" size={16} />
              {busyAction === "run-cancel" ? "Cancelling" : "Cancel Run"}
            </button>
          ) : null}
          {synthesisCancellable ? (
            <button
              className="icon-button danger"
              disabled={
                !actionReady
                || busyAction !== ""
                || !cancelPayload.operation_id
                || !cancelPayload.request_hash
              }
              title={!actionReady ? actionState : "Cancel workflow synthesis"}
              type="button"
              onClick={() => onCancel(cancelPayload)}
            >
              <CircleStop aria-hidden="true" size={16} />
              {busyAction === "workflow-cancel" ? "Cancelling" : "Cancel synthesis"}
            </button>
          ) : null}
          {submitted || terminal ? (
            <button className="icon-button" type="button" onClick={onOpenRuns}>
              <ExternalLink aria-hidden="true" size={16} />
              Runs
            </button>
          ) : null}
          {changeMode === "config_change" && !configApplied ? (
            <button
              className="icon-button"
              disabled={!actionReady || !approvable || busyAction !== "" || !validationResultRef.ref}
              title={!actionReady ? actionState : "Validate CAS and apply this proposal to zf.yaml"}
              type="button"
              onClick={() => onApply(configApplyPayload)}
            >
              <FileDiff aria-hidden="true" size={16} />
              {busyAction === "workflow-config-apply" ? "Applying" : "Apply config"}
            </button>
          ) : null}
          <button
            className="icon-button danger"
            disabled={!actionReady || !approvable || busyAction !== ""}
            title={!actionReady ? actionState : "Reject this exact proposal"}
            type="button"
            onClick={() => onReject({
              ...decisionPayload,
              reason: "operator rejected proposal",
            })}
          >
            <X aria-hidden="true" size={16} />
            {busyAction === "workflow-reject" ? "Rejecting" : "Reject"}
          </button>
          <button
            className="icon-button primary"
            disabled={
              !actionReady
              || !approvable
              || busyAction !== ""
              || (changeMode === "config_change" && !configApplied)
            }
            title={
              !actionReady
                ? actionState
                : changeMode === "config_change" && !configApplied
                  ? "Apply the approved config change first"
                  : "Approve this exact proposal and start its run"
            }
            type="button"
            onClick={() => onApprove(submitPayload)}
          >
            <Play aria-hidden="true" size={16} />
            {busyAction === "workflow-submit" ? "Starting" : "Approve & Run"}
          </button>
        </div>
      </header>

      <section className="workflow-proposal-requirement">
        <WorkflowList title="Acceptance" items={asStringArray(requirement.acceptance)} />
        <WorkflowList title="Constraints" items={asStringArray(requirement.constraints)} />
        <WorkflowList title="Open questions" items={asStringArray(requirement.open_questions)} />
      </section>

      <section className="workflow-proposal-section">
        <SectionHeading
          icon={GitFork}
          meta={`${stages.length} stages / expected topology`}
          title="Proposal Graph"
        />
        <div className="workflow-proposal-stage-graph" data-testid="workflow-proposal-graph">
          {stages.length ? stages.map((stage, index) => (
            <div className="workflow-proposal-stage" key={textValue(stage.id) || String(index)}>
              <span>{index + 1}</span>
              <strong>{textValue(stage.id) || `stage-${index + 1}`}</strong>
              <small>
                {textValue(stage.topology) || "direct"}
                {stage.role ? ` / ${textValue(stage.role)}` : ""}
              </small>
            </div>
          )) : <span className="muted">No declared stages</span>}
        </div>
      </section>

      {(blockers.length || warnings.length) ? (
        <section className="workflow-proposal-section">
          <SectionHeading
            icon={ShieldCheck}
            meta={`${blockers.length} blockers / ${warnings.length} warnings`}
            title="Preflight"
          />
          <div className="workflow-proposal-diagnostics">
            {[...blockers, ...warnings].map((item, index) => (
              <div
                className={textValue(item.severity).toUpperCase() === "STOP" ? "tone-error" : "tone-warn"}
                key={`${textValue(item.kind)}-${index}`}
              >
                <strong>{textValue(item.kind) || textValue(item.title) || "diagnostic"}</strong>
                <span>{textValue(item.message) || textValue(item.reason)}</span>
              </div>
            ))}
          </div>
        </section>
      ) : null}

      <div className="workflow-proposal-two-column">
        <section className="workflow-proposal-section">
          <SectionHeading icon={Check} meta={`${roles.length} roles`} title="Execution Closure" />
          <div className="workflow-proposal-role-list">
            {roles.map((role, index) => {
              const execution = asRecord(role.execution);
              return (
                <div key={`${textValue(role.name)}-${index}`}>
                  <strong>{textValue(role.name) || textValue(role.instance_id) || "role"}</strong>
                  <span>{textValue(role.backend) || "default backend"}</span>
                  <code>{textValue(execution.default_profile) || "direct-v1"}</code>
                </div>
              );
            })}
          </div>
          <dl className="workflow-proposal-kv">
            <dt>Profiles</dt>
            <dd>{profiles.map(([id]) => id).join(", ") || "direct-v1"}</dd>
            <dt>Delivery policy</dt>
            <dd>{textValue(completionProfile.delivery_policy) || "report_only"}</dd>
            <dt>Stages</dt>
            <dd>{textValue(estimated.stages) || stages.length}</dd>
            <dt>Roles</dt>
            <dd>{textValue(estimated.roles) || roles.length}</dd>
          </dl>
        </section>

        <section className="workflow-proposal-section">
          <SectionHeading icon={ShieldCheck} meta={changeMode} title="Decision Binding" />
          <dl className="workflow-proposal-kv">
            <dt>Proposal</dt>
            <dd className="mono">{shortDigest(proposalDigest)}</dd>
            <dt>Requirement</dt>
            <dd className="mono">{shortDigest(textValue(request.requirement_spec_digest))}</dd>
            <dt>Effective config</dt>
            <dd className="mono">{shortDigest(textValue(asRecord(proposal.effective_config_ref).sha256))}</dd>
            <dt>Config apply</dt>
            <dd>{changeMode === "config_change" ? (configApplied ? "applied" : "required") : "not required"}</dd>
            <dt>Run</dt>
            <dd>{terminal || (submitted ? "submitted" : "not started")}</dd>
          </dl>
        </section>
      </div>

      <section className="workflow-proposal-section">
        <SectionHeading icon={FileDiff} meta={textValue(configDiff.changed) === "true" ? "changed" : changeMode} title="Config Diff" />
        <pre className="workflow-proposal-code">
          {textValue(configDiff.unified_diff) || "No zf.yaml changes"}
        </pre>
      </section>

      <section className="workflow-proposal-section">
        <SectionHeading icon={GitFork} meta="short FlowSpec" title="FlowSpec" />
        <pre className="workflow-proposal-code">{stringify(shortFlowSpec)}</pre>
      </section>
    </>
  );
}

function SectionHeading({
  icon: Icon,
  meta,
  title,
}: {
  icon: typeof GitFork;
  meta: string;
  title: string;
}) {
  return (
    <div className="workflow-proposal-section-head">
      <span><Icon aria-hidden="true" size={15} /><strong>{title}</strong></span>
      <small>{meta}</small>
    </div>
  );
}

function WorkflowList({ items, title }: { items: string[]; title: string }) {
  return (
    <div>
      <strong>{title}</strong>
      {items.length ? (
        <ul>{items.map((item) => <li key={item}>{item}</li>)}</ul>
      ) : <span className="muted">None</span>}
    </div>
  );
}

function StatusBadge({ status }: { status: string }) {
  const normalized = status || "unknown";
  const tone = (
    ["running", "submitted", "approved"].includes(normalized)
      ? "info"
      : ["proposed", "ready", "draft"].includes(normalized)
        ? "warn"
        : normalized === "rejected"
          ? "err"
          : "muted"
  );
  return <span className={`badge badge-${tone}`}>{normalized}</span>;
}

function WorkflowEmpty({ title }: { title: string }) {
  return (
    <div className="workflow-proposal-empty">
      <GitFork aria-hidden="true" size={24} />
      <strong>{title}</strong>
    </div>
  );
}

function requestTitle(request: WorkflowRequest): string {
  return textValue(request.objective)
    || textValue(request.title)
    || textValue(request.request_id)
    || "Workflow request";
}

function shortDigest(value: string): string {
  return value ? value.slice(0, 12) : "-";
}

function decisionFeedback(result: ActionResponse): string {
  if (result.action === "workflow-submit" && result.ok) return "Run submitted.";
  if (result.action === "workflow-reject" && result.ok) return "Proposal rejected.";
  if (result.action === "workflow-config-apply" && result.ok) return "Config applied.";
  return result.ok ? "Action completed." : "Action failed.";
}
