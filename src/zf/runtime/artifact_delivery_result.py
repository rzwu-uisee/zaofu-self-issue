"""Typed result and mechanical bindings for artifact-only Goal delivery."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable
from pathlib import Path
from typing import Any, Mapping

from zf.runtime.call_result_envelope import call_result_envelope_ref
from zf.runtime.sidecar_refs import SidecarRefError, hydrate_sidecar_ref, sidecar_path


SCHEMA_VERSION = "artifact-delivery-result.v1"
COMPLETION_PROFILE = "artifact_delivery"
VERDICTS = frozenset({"passed", "rejected", "blocked"})
CLAIM_STATUSES = frozenset({"closed", "waived", "open", "blocked"})
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_BRIEFING_IDENTITY_FIELDS = (
    "workflow_run_id",
    "goal_id",
    "workflow_generation",
    "request_revision",
    "generic_workflow_contract_digest",
    "workflow_intent",
    "workflow_template",
    "completion_profile",
    "run_contract_ref",
    "run_contract_digest",
    "goal_claim_set_ref",
    "goal_claim_set_digest",
)


class ArtifactDeliveryResultError(ValueError):
    """An artifact delivery result is malformed or cannot be bound."""


def artifact_delivery_success_payload(
    child_payload: Mapping[str, Any],
    *,
    verifier_stage_id: str,
    verifier_role: str,
    state_dir: Path | None = None,
) -> dict[str, Any]:
    """Build the editable result-submit template for an artifact verifier."""

    missing = [
        field
        for field in _BRIEFING_IDENTITY_FIELDS
        if child_payload.get(field) in (None, "", [], {})
    ]
    if missing:
        raise ArtifactDeliveryResultError(
            "artifact delivery briefing identity missing: "
            + ", ".join(missing)
        )
    revision = child_payload.get("request_revision")
    if (
        isinstance(revision, bool)
        or not isinstance(revision, int)
        or revision <= 0
    ):
        raise ArtifactDeliveryResultError(
            "artifact delivery briefing request_revision must be positive"
        )
    for field in (
        "workflow_generation",
        "generic_workflow_contract_digest",
        "run_contract_digest",
        "goal_claim_set_digest",
    ):
        if not _SHA256.fullmatch(str(child_payload.get(field) or "")):
            raise ArtifactDeliveryResultError(
                f"artifact delivery briefing {field} must be a sha256 digest"
            )
    expected_artifacts = [
        dict(item)
        for item in child_payload.get("required_delivery_artifacts") or []
        if isinstance(item, Mapping)
    ]
    input_result_refs = _strings(child_payload.get("input_result_refs"))
    if not expected_artifacts:
        raise ArtifactDeliveryResultError(
            "artifact delivery briefing has no required artifacts"
        )
    for index, item in enumerate(expected_artifacts):
        missing_artifact_fields = [
            field
            for field in ("name", "kind", "source_ref")
            if not str(item.get(field) or "").strip()
        ]
        if missing_artifact_fields:
            raise ArtifactDeliveryResultError(
                f"artifact delivery briefing artifact[{index}] missing: "
                + ", ".join(missing_artifact_fields)
            )
    if not input_result_refs:
        raise ArtifactDeliveryResultError(
            "artifact delivery briefing has no admitted input results"
        )
    if any(
        not call_result_envelope_ref(ref)
        for ref in input_result_refs
    ):
        raise ArtifactDeliveryResultError(
            "artifact delivery briefing inputs must be admitted call-result "
            "envelopes"
        )
    resolved_artifacts: dict[str, dict[str, Any]] = {}
    if state_dir is not None:
        from zf.runtime.generic_workflow_outputs import (
            resolve_declared_output_artifact_index,
        )

        resolved_artifacts, missing_sources = (
            resolve_declared_output_artifact_index(
                state_dir,
                input_result_refs=input_result_refs,
                required_artifacts=expected_artifacts,
            )
        )
        if missing_sources:
            raise ArtifactDeliveryResultError(
                "artifact delivery inputs do not contain required immutable "
                "outputs: " + ", ".join(missing_sources)
            )
    payload = {
        key: child_payload[key]
        for key in (
            "workflow_run_id",
            "goal_id",
            "workflow_generation",
            "request_revision",
            "generic_workflow_contract_digest",
            "workflow_intent",
            "run_contract_ref",
            "run_contract_digest",
            "goal_claim_set_ref",
            "goal_claim_set_digest",
            "workflow_template",
            "completion_profile",
        )
        if child_payload.get(key) not in (None, "")
    }
    payload.update({
        "verifier_stage_id": verifier_stage_id,
        "verifier_role": verifier_role,
        "artifact_delivery_result": {
            "schema_version": SCHEMA_VERSION,
            "workflow_run_id": str(
                child_payload.get("workflow_run_id") or ""
            ),
            "goal_id": str(child_payload.get("goal_id") or ""),
            "workflow_generation": str(
                child_payload.get("workflow_generation") or ""
            ),
            "request_revision": int(
                child_payload.get("request_revision") or 0
            ),
            "generic_workflow_contract_digest": str(
                child_payload.get("generic_workflow_contract_digest") or ""
            ),
            "run_contract_ref": str(
                child_payload.get("run_contract_ref") or ""
            ),
            "run_contract_digest": str(
                child_payload.get("run_contract_digest") or ""
            ),
            "completion_profile": COMPLETION_PROFILE,
            "verifier_stage_id": verifier_stage_id,
            "verifier_role": verifier_role,
            "goal_claim_set_ref": str(
                child_payload.get("goal_claim_set_ref") or ""
            ),
            "goal_claim_set_digest": str(
                child_payload.get("goal_claim_set_digest") or ""
            ),
            "verdict": "passed",
            "artifacts": [
                resolved_artifacts.get(
                    str(item.get("source_ref") or ""),
                    {
                        "name": str(item.get("name") or ""),
                        "kind": str(item.get("kind") or ""),
                        "source_ref": str(item.get("source_ref") or ""),
                        "producer_stage_id": str(
                            item.get("source_ref") or ""
                        ).split(".", 1)[0],
                        "ref": "<replace with immutable sidecar ref>",
                        "sha256": "<replace with sidecar sha256>",
                    },
                )
                for item in expected_artifacts
            ],
            "goal_coverage": [{
                "goal_claim_id": "<replace with mandatory claim id>",
                "status": "closed",
                "supporting_artifact_refs": [
                    "<replace with delivered artifact ref>"
                ],
            }],
            "input_result_refs": input_result_refs,
            "verification_evidence_refs": [
                "<replace with verification evidence ref>"
            ],
            "open_gap_refs": [],
            "recommended_action": "complete",
            "summary": (
                "Required artifacts satisfy all mandatory Goal claims."
            ),
        },
    })
    return payload


def artifact_delivery_dossier_projection(
    events: Iterable[Any],
) -> dict[str, Any]:
    from zf.runtime.artifact_delivery_dossier import (
        project_artifact_delivery_dossier,
    )

    return project_artifact_delivery_dossier(
        events,
        result_schema_version=SCHEMA_VERSION,
    )


def normalize_artifact_delivery_result(
    payload: Mapping[str, Any],
) -> dict[str, Any]:
    raw = payload.get("artifact_delivery_result")
    if not isinstance(raw, Mapping) and isinstance(payload.get("report"), Mapping):
        raw = payload["report"].get("artifact_delivery_result")
    result = dict(raw) if isinstance(raw, Mapping) else dict(payload)
    for key in (
        "workflow_run_id",
        "goal_id",
        "workflow_generation",
        "request_revision",
        "generic_workflow_contract_digest",
        "run_contract_ref",
        "run_contract_digest",
        "completion_profile",
        "goal_claim_set_ref",
        "goal_claim_set_digest",
    ):
        if result.get(key) in (None, "") and payload.get(key) not in (None, ""):
            result[key] = payload[key]
    if not str(result.get("verifier_stage_id") or "").strip():
        result["verifier_stage_id"] = str(
            payload.get("verifier_stage_id") or payload.get("stage_id") or ""
        )
    if not str(result.get("verifier_role") or "").strip():
        result["verifier_role"] = str(
            payload.get("verifier_role") or payload.get("role_instance") or ""
        )
    result.setdefault("schema_version", SCHEMA_VERSION)
    result.setdefault("completion_profile", COMPLETION_PROFILE)
    result["artifacts"] = _objects(result.get("artifacts"))
    result["goal_coverage"] = _objects(result.get("goal_coverage"))
    result["input_result_refs"] = _strings(result.get("input_result_refs"))
    result["verification_evidence_refs"] = _strings(
        result.get("verification_evidence_refs")
    )
    result["open_gap_refs"] = _strings(result.get("open_gap_refs"))
    validate_artifact_delivery_result(result)
    return result


def validate_artifact_delivery_result(result: Mapping[str, Any]) -> None:
    if str(result.get("schema_version") or "") != SCHEMA_VERSION:
        raise ArtifactDeliveryResultError(
            f"schema_version must be {SCHEMA_VERSION!r}"
        )
    if str(result.get("completion_profile") or "") != COMPLETION_PROFILE:
        raise ArtifactDeliveryResultError(
            f"completion_profile must be {COMPLETION_PROFILE!r}"
        )
    required = (
        "workflow_run_id",
        "goal_id",
        "workflow_generation",
        "request_revision",
        "generic_workflow_contract_digest",
        "run_contract_ref",
        "run_contract_digest",
        "goal_claim_set_ref",
        "goal_claim_set_digest",
        "verifier_stage_id",
        "verifier_role",
        "summary",
    )
    missing = [
        field
        for field in required
        if result.get(field) in (None, "", [], {})
    ]
    if missing:
        raise ArtifactDeliveryResultError(
            "artifact delivery result missing: " + ", ".join(missing)
        )
    revision = result.get("request_revision")
    if isinstance(revision, bool) or not isinstance(revision, int) or revision <= 0:
        raise ArtifactDeliveryResultError(
            "request_revision must be a positive integer"
        )
    for field in (
        "workflow_generation",
        "generic_workflow_contract_digest",
        "run_contract_digest",
    ):
        if not _SHA256.fullmatch(str(result.get(field) or "")):
            raise ArtifactDeliveryResultError(
                f"{field} must be a lowercase sha256 digest"
            )
    verdict = str(result.get("verdict") or "").lower()
    if verdict not in VERDICTS:
        raise ArtifactDeliveryResultError(f"invalid verdict {verdict!r}")

    artifacts = result.get("artifacts")
    if not isinstance(artifacts, list) or not artifacts:
        raise ArtifactDeliveryResultError("artifacts must be a non-empty list")
    seen_names: set[str] = set()
    seen_sources: set[str] = set()
    seen_refs: set[str] = set()
    verifier_stage_id = str(result.get("verifier_stage_id") or "")
    for index, raw in enumerate(artifacts):
        if not isinstance(raw, Mapping):
            raise ArtifactDeliveryResultError(
                f"artifacts[{index}] must be an object"
            )
        item = dict(raw)
        for field in (
            "name",
            "kind",
            "source_ref",
            "producer_stage_id",
            "ref",
            "sha256",
        ):
            if not str(item.get(field) or "").strip():
                raise ArtifactDeliveryResultError(
                    f"artifacts[{index}] missing {field}"
                )
        digest = str(item.get("sha256") or "")
        if not _SHA256.fullmatch(digest):
            raise ArtifactDeliveryResultError(
                f"artifacts[{index}].sha256 must be a lowercase sha256 digest"
            )
        source_ref = str(item.get("source_ref") or "")
        producer = str(item.get("producer_stage_id") or "")
        if not source_ref.startswith(f"{producer}."):
            raise ArtifactDeliveryResultError(
                f"artifacts[{index}] source_ref is not produced by "
                f"{producer!r}"
            )
        if producer == verifier_stage_id:
            raise ArtifactDeliveryResultError(
                "independent verifier cannot produce a required artifact"
            )
        name = str(item.get("name") or "")
        ref = str(item.get("ref") or "")
        for value, seen, label in (
            (name, seen_names, "name"),
            (source_ref, seen_sources, "source_ref"),
            (ref, seen_refs, "ref"),
        ):
            if value in seen:
                raise ArtifactDeliveryResultError(
                    f"duplicate artifact {label} {value!r}"
                )
            seen.add(value)

    coverage = result.get("goal_coverage")
    if not isinstance(coverage, list) or not coverage:
        raise ArtifactDeliveryResultError(
            "goal_coverage must be a non-empty list"
        )
    artifact_refs = {
        str(item.get("ref") or "")
        for item in artifacts
        if isinstance(item, Mapping)
    }
    seen_claims: set[str] = set()
    claim_statuses: set[str] = set()
    for index, raw in enumerate(coverage):
        if not isinstance(raw, Mapping):
            raise ArtifactDeliveryResultError(
                f"goal_coverage[{index}] must be an object"
            )
        claim_id = str(raw.get("goal_claim_id") or "").strip()
        status = str(raw.get("status") or "").strip().lower()
        if not claim_id:
            raise ArtifactDeliveryResultError(
                f"goal_coverage[{index}] missing goal_claim_id"
            )
        if claim_id in seen_claims:
            raise ArtifactDeliveryResultError(
                f"duplicate goal claim {claim_id!r}"
            )
        if status not in CLAIM_STATUSES:
            raise ArtifactDeliveryResultError(
                f"goal_coverage[{index}] has invalid status {status!r}"
            )
        seen_claims.add(claim_id)
        claim_statuses.add(status)
        supporting = _strings(raw.get("supporting_artifact_refs"))
        if status == "closed" and not supporting:
            raise ArtifactDeliveryResultError(
                f"goal_coverage[{index}] closed without supporting artifacts"
            )
        unknown = sorted(set(supporting) - artifact_refs)
        if unknown:
            raise ArtifactDeliveryResultError(
                f"goal_coverage[{index}] references unknown artifacts {unknown}"
            )
        if status == "waived" and not str(raw.get("waiver_ref") or "").strip():
            raise ArtifactDeliveryResultError(
                f"goal_coverage[{index}] waived without waiver_ref"
            )

    gaps = _strings(result.get("open_gap_refs"))
    if not _strings(result.get("input_result_refs")):
        raise ArtifactDeliveryResultError(
            "input_result_refs must reference admitted stage results"
        )
    if not _strings(result.get("verification_evidence_refs")):
        raise ArtifactDeliveryResultError(
            "verification_evidence_refs must not be empty"
        )
    action = str(result.get("recommended_action") or "").strip().lower()
    allowed_actions = {
        "passed": {"complete"},
        "rejected": {"gap_plan", "replan"},
        "blocked": {"human", "hold"},
    }
    if action not in allowed_actions[verdict]:
        raise ArtifactDeliveryResultError(
            f"recommended_action {action!r} is invalid for {verdict}"
        )
    if verdict == "passed" and claim_statuses - {"closed", "waived"}:
        raise ArtifactDeliveryResultError(
            "passed verdict contains open or blocked claims"
        )
    if verdict == "passed" and gaps:
        raise ArtifactDeliveryResultError(
            "passed verdict cannot contain open_gap_refs"
        )
    if verdict == "rejected" and not gaps:
        raise ArtifactDeliveryResultError(
            "rejected verdict requires open_gap_refs"
        )


def artifact_delivery_admission_issues(
    state_dir: Path,
    result: Mapping[str, Any],
    *,
    events: Iterable[Any] = (),
) -> list[dict[str, str]]:
    """Bind one typed result to immutable artifacts and its Run Contract."""

    rows = list(events)
    issues: list[dict[str, str]] = []
    for index, item in enumerate(result.get("artifacts") or []):
        if not isinstance(item, Mapping):
            continue
        try:
            hydrate_sidecar_ref(Path(state_dir), dict(item))
        except (OSError, SidecarRefError, ValueError) as exc:
            issues.append({
                "field": f"control_result.artifacts[{index}]",
                "code": getattr(exc, "code", "artifact_unreadable"),
                "message": str(exc),
            })

    claim_ref = str(result.get("goal_claim_set_ref") or "").strip()
    claim_digest = str(result.get("goal_claim_set_digest") or "").strip()
    if bool(claim_ref) != bool(claim_digest):
        issues.append({
            "field": "control_result.goal_claim_set_ref",
            "code": "claim_set_binding_incomplete",
        })
    if claim_ref:
        try:
            claim_set = hydrate_sidecar_ref(
                Path(state_dir),
                {"ref": claim_ref, "sha256": claim_digest},
            ).payload
        except (OSError, SidecarRefError, ValueError) as exc:
            issues.append({
                "field": "control_result.goal_claim_set_ref",
                "code": getattr(exc, "code", "claim_set_unreadable"),
                "message": str(exc),
            })
        else:
            if isinstance(claim_set, Mapping):
                issues.extend(_goal_claim_set_issues(result, claim_set))
            else:
                issues.append({
                    "field": "control_result.goal_claim_set_ref",
                    "code": "claim_set_invalid",
                })

    try:
        contract = load_bound_run_contract(Path(state_dir), result)
    except ArtifactDeliveryResultError as exc:
        issues.append({
            "field": "control_result.run_contract_ref",
            "code": "run_contract_binding_invalid",
            "message": str(exc),
        })
    else:
        issues.extend(artifact_delivery_contract_issues(result, contract))
    issues.extend(_runtime_binding_issues(
        Path(state_dir),
        result,
        events=rows,
    ))
    return issues


def load_bound_run_contract(
    state_dir: Path,
    result: Mapping[str, Any],
) -> dict[str, Any]:
    ref = str(result.get("run_contract_ref") or "").strip()
    if not ref:
        raise ArtifactDeliveryResultError("run_contract_ref is missing")
    try:
        path = sidecar_path(state_dir, ref)
        raw = path.read_bytes()
        from zf.runtime.run_contract import load_run_contract_snapshot

        snapshot = load_run_contract_snapshot(
            state_dir,
            {
                "ref": ref,
                "sha256": hashlib.sha256(raw).hexdigest(),
                "byte_count": len(raw),
            },
        )
    except (OSError, SidecarRefError, ValueError) as exc:
        raise ArtifactDeliveryResultError(
            f"run contract snapshot cannot be verified: {exc}"
        ) from exc
    expected = str(result.get("run_contract_digest") or "")
    actual = str(snapshot.get("contract_digest") or "")
    if not expected or expected != actual:
        raise ArtifactDeliveryResultError(
            "run contract semantic digest does not match the result"
        )
    contract = snapshot.get("contract")
    if not isinstance(contract, Mapping):
        raise ArtifactDeliveryResultError("run contract body is missing")
    return dict(contract)


def artifact_delivery_contract_issues(
    result: Mapping[str, Any],
    contract: Mapping[str, Any],
) -> list[dict[str, str]]:
    workflow = (
        contract.get("workflow")
        if isinstance(contract.get("workflow"), Mapping)
        else {}
    )
    issues: list[dict[str, str]] = []
    expected = {
        "completion_profile": str(
            workflow.get("completion_profile") or ""
        ),
        "generic_workflow_contract_digest": str(
            workflow.get("generic_workflow_contract_digest") or ""
        ),
        "workflow_generation": str(
            workflow.get("proposal_digest")
            or workflow.get("generic_workflow_contract_digest")
            or ""
        ),
    }
    for field, value in expected.items():
        if value and str(result.get(field) or "") != value:
            issues.append({
                "field": f"control_result.{field}",
                "code": "identity_mismatch",
                "message": f"expected {value}, got {result.get(field) or ''}",
            })
    if expected["completion_profile"] != COMPLETION_PROFILE:
        issues.append({
            "field": "run_contract.workflow.completion_profile",
            "code": "profile_mismatch",
            "message": "Run Contract does not authorize artifact delivery",
        })

    expected_artifacts = [
        dict(item)
        for item in contract.get("required_delivery_artifacts") or []
        if isinstance(item, Mapping)
        and str(item.get("source_ref") or "").strip()
    ]
    provided = {
        str(item.get("source_ref") or ""): item
        for item in result.get("artifacts") or []
        if isinstance(item, Mapping)
    }
    expected_sources = {
        str(item.get("source_ref") or "") for item in expected_artifacts
    }
    for item in expected_artifacts:
        source_ref = str(item.get("source_ref") or "")
        artifact = provided.get(source_ref)
        if artifact is None:
            issues.append({
                "field": "control_result.artifacts",
                "code": "required_artifact_missing",
                "message": source_ref,
            })
            continue
        for field in ("name", "kind"):
            value = str(item.get(field) or "")
            if value and str(artifact.get(field) or "") != value:
                issues.append({
                    "field": f"control_result.artifacts.{source_ref}.{field}",
                    "code": "artifact_contract_mismatch",
                    "message": (
                        f"expected {value}, got {artifact.get(field) or ''}"
                    ),
                })
    extras = sorted(set(provided) - expected_sources)
    for source_ref in extras:
        issues.append({
            "field": "control_result.artifacts",
            "code": "undeclared_delivery_artifact",
            "message": source_ref,
        })

    generic = (
        workflow.get("generic_workflow_contract")
        if isinstance(workflow.get("generic_workflow_contract"), Mapping)
        else {}
    )
    tasks = [
        item for item in generic.get("tasks") or []
        if isinstance(item, Mapping)
    ]
    task_by_name = {
        str(item.get("name") or ""): item
        for item in tasks
        if str(item.get("name") or "")
    }
    for artifact in result.get("artifacts") or []:
        if not isinstance(artifact, Mapping):
            continue
        source_ref = str(artifact.get("source_ref") or "")
        producer_stage = str(artifact.get("producer_stage_id") or "")
        expected_producer = source_ref.partition(".")[0]
        producer = task_by_name.get(producer_stage)
        if producer_stage != expected_producer or producer is None:
            issues.append({
                "field": "control_result.artifacts",
                "code": "artifact_producer_not_authorized",
                "message": producer_stage,
            })
            continue
        output_name = source_ref.partition(".")[2]
        output = next(
            (
                item
                for item in producer.get("outputs") or []
                if isinstance(item, Mapping)
                and str(item.get("name") or "") == output_name
            ),
            None,
        )
        if not isinstance(output, Mapping) or str(
            output.get("kind") or ""
        ) != str(artifact.get("kind") or ""):
            issues.append({
                "field": "control_result.artifacts",
                "code": "artifact_producer_output_mismatch",
                "message": source_ref,
            })
    verifier_stage = str(result.get("verifier_stage_id") or "")
    verifier = next(
        (
            item for item in tasks
            if str(item.get("name") or "") == verifier_stage
        ),
        None,
    )
    if not isinstance(verifier, Mapping) or str(
        verifier.get("operation") or ""
    ) != "agent.verify":
        issues.append({
            "field": "control_result.verifier_stage_id",
            "code": "independent_verify_not_authorized",
            "message": verifier_stage,
        })
    else:
        verifier_role = str(result.get("verifier_role") or "")
        authorized_roles = {
            str(item)
            for item in verifier.get("roles") or []
            if str(item).strip()
        }
        if verifier_role not in authorized_roles:
            issues.append({
                "field": "control_result.verifier_role",
                "code": "independent_verify_role_not_authorized",
                "message": verifier_role,
            })
        consumed = {
            str(item.get("source") or "")
            for item in verifier.get("inputs") or []
            if isinstance(item, Mapping)
        }
        missing_inputs = sorted(expected_sources - consumed)
        for source_ref in missing_inputs:
            issues.append({
                "field": "control_result.verifier_stage_id",
                "code": "required_artifact_not_verified",
                "message": source_ref,
            })
    return issues


def _runtime_binding_issues(
    state_dir: Path,
    result: Mapping[str, Any],
    *,
    events: list[Any],
) -> list[dict[str, str]]:
    workflow_run_id = str(result.get("workflow_run_id") or "")
    workflow_generation = str(result.get("workflow_generation") or "")
    goal_id = str(result.get("goal_id") or "")
    admitted_refs: set[str] = set()
    from zf.runtime.call_result_envelope import (
        CallResultEnvelopeError,
        hydrate_call_result_envelope,
    )

    admitted_envelopes: dict[str, Mapping[str, Any]] = {}
    for event in events:
        if str(getattr(event, "type", "") or "") != (
            "workflow.call.result.admitted"
        ):
            continue
        payload = _event_payload(event)
        if _event_run_id(event, payload) != workflow_run_id:
            continue
        descriptor = payload.get("envelope_ref")
        if not isinstance(descriptor, Mapping):
            continue
        try:
            envelope = hydrate_call_result_envelope(
                state_dir,
                dict(descriptor),
            )
        except (CallResultEnvelopeError, OSError, ValueError):
            continue
        identity = (
            envelope.get("identity")
            if isinstance(envelope.get("identity"), Mapping)
            else {}
        )
        admitted_generation = str(
            identity.get("workflow_generation") or ""
        )
        if (
            admitted_generation
            and admitted_generation != workflow_generation
        ):
            continue
        ref = str(descriptor.get("ref") or "")
        if ref:
            admitted_refs.add(ref)
            admitted_envelopes[ref] = envelope

    issues: list[dict[str, str]] = []
    input_result_refs = _strings(result.get("input_result_refs"))
    for ref in input_result_refs:
        if ref not in admitted_refs:
            issues.append({
                "field": "control_result.input_result_refs",
                "code": "result_not_admitted",
                "message": ref,
            })

    from zf.runtime.artifact_delivery_evidence import (
        artifact_delivery_evidence_binding_issues,
    )

    issues.extend(artifact_delivery_evidence_binding_issues(
        result,
        admitted_envelopes=admitted_envelopes,
    ))

    current_generation = ""
    latest_claim_pin: Mapping[str, Any] | None = None
    for event in reversed(events):
        payload = _event_payload(event)
        if _event_run_id(event, payload) != workflow_run_id:
            continue
        event_type = str(getattr(event, "type", "") or "")
        if not current_generation and event_type == "workflow.invoke.requested":
            current_generation = str(
                payload.get("workflow_generation")
                or payload.get("workflow_proposal_digest")
                or ""
            )
        if (
            latest_claim_pin is None
            and event_type == "goal.claim_set.pinned"
            and str(payload.get("goal_id") or "") == goal_id
        ):
            latest_claim_pin = payload
        if current_generation and latest_claim_pin is not None:
            break
    if current_generation and current_generation != workflow_generation:
        issues.append({
            "field": "control_result.workflow_generation",
            "code": "stale_workflow_generation",
            "message": (
                f"current generation is {current_generation}, got "
                f"{workflow_generation}"
            ),
        })
    if latest_claim_pin is not None:
        expected_generation = str(
            latest_claim_pin.get("task_map_generation") or ""
        )
        if (
            expected_generation
            and str(result.get("workflow_generation") or "")
            != expected_generation
        ):
            issues.append({
                "field": "control_result.workflow_generation",
                "code": "stale_claim_set_identity",
                "message": (
                    f"expected {expected_generation}, got "
                    f"{result.get('workflow_generation') or ''}"
                ),
            })
        expected_ref = str(
            latest_claim_pin.get("goal_claim_set_ref") or ""
        )
        if (
            expected_ref
            and str(result.get("goal_claim_set_ref") or "") != expected_ref
        ):
            issues.append({
                "field": "control_result.goal_claim_set_ref",
                "code": "stale_claim_set_identity",
                "message": (
                    f"expected {expected_ref}, got "
                    f"{result.get('goal_claim_set_ref') or ''}"
                ),
            })
        expected_digest = str(
            latest_claim_pin.get("goal_claim_set_digest") or ""
        )
        actual_digest = str(result.get("goal_claim_set_digest") or "")
        if expected_digest and actual_digest != expected_digest:
            content_digest = str(
                latest_claim_pin.get("goal_claim_set_content_digest") or ""
            )
            issues.append({
                "field": "control_result.goal_claim_set_digest",
                "code": (
                    "claim_set_digest_kind_mismatch"
                    if content_digest and actual_digest == content_digest
                    else "claim_set_digest_mismatch"
                ),
                "message": (
                    f"expected sidecar digest {expected_digest}, got "
                    f"{actual_digest}"
                ),
            })
    return issues


def _event_payload(event: Any) -> Mapping[str, Any]:
    payload = getattr(event, "payload", {})
    return payload if isinstance(payload, Mapping) else {}


def _event_run_id(event: Any, payload: Mapping[str, Any]) -> str:
    return str(
        payload.get("workflow_run_id")
        or payload.get("run_id")
        or getattr(event, "correlation_id", "")
        or ""
    )


def _goal_claim_set_issues(
    result: Mapping[str, Any],
    claim_set: Mapping[str, Any],
) -> list[dict[str, str]]:
    from zf.runtime.goal_claim_set import (
        GoalClaimSetError,
        validate_goal_claim_set,
    )

    try:
        validate_goal_claim_set(
            claim_set,
            workflow_run_id=str(result.get("workflow_run_id") or ""),
            goal_id=str(result.get("goal_id") or ""),
            task_map_generation=str(
                result.get("workflow_generation") or ""
            ),
        )
    except GoalClaimSetError as exc:
        return [{
            "field": "control_result.goal_claim_set_ref",
            "code": "claim_set_identity_invalid",
            "message": str(exc),
        }]
    claims = [
        item for item in claim_set.get("claims") or []
        if isinstance(item, Mapping)
    ]
    known = {
        str(item.get("goal_claim_id") or "")
        for item in claims
        if str(item.get("goal_claim_id") or "")
    }
    mandatory = {
        str(item.get("goal_claim_id") or "")
        for item in claims
        if bool(item.get("mandatory", True))
        and str(item.get("goal_claim_id") or "")
    }
    coverage = [
        item for item in result.get("goal_coverage") or []
        if isinstance(item, Mapping)
    ]
    actual = {
        str(item.get("goal_claim_id") or "")
        for item in coverage
        if str(item.get("goal_claim_id") or "")
    }
    issues: list[dict[str, str]] = []
    for claim_id in sorted(mandatory - actual):
        issues.append({
            "field": "control_result.goal_coverage",
            "code": "mandatory_claim_missing",
            "message": claim_id,
        })
    for claim_id in sorted(actual - known):
        issues.append({
            "field": "control_result.goal_coverage",
            "code": "unknown_claim",
            "message": claim_id,
        })
    return issues


def _strings(value: Any) -> list[str]:
    values = (
        value
        if isinstance(value, (list, tuple, set))
        else [value] if value else []
    )
    return list(
        dict.fromkeys(
            str(item).strip() for item in values if str(item).strip()
        )
    )


def _objects(value: Any) -> list[dict[str, Any]]:
    return [
        dict(item)
        for item in value if isinstance(item, Mapping)
    ] if isinstance(value, list) else []


__all__ = [
    "ArtifactDeliveryResultError",
    "COMPLETION_PROFILE",
    "SCHEMA_VERSION",
    "artifact_delivery_admission_issues",
    "artifact_delivery_contract_issues",
    "artifact_delivery_dossier_projection",
    "artifact_delivery_success_payload",
    "load_bound_run_contract",
    "normalize_artifact_delivery_result",
    "validate_artifact_delivery_result",
]
