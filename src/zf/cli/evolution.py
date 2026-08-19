"""CLI entrypoints for deterministic self-evolution lifecycle operations."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any

from zf.core.config.project_context import resolve_project_context
from zf.core.events.factory import event_log_from_project
from zf.core.events.writer import EventWriter
from zf.runtime.evolution_coordinator import EvolutionCoordinator
from zf.runtime.evolution_evaluator import SealedEvaluatorAuthority


def register(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser(
        "evolution",
        help="Manage evidence-bound self-evolution attempts and capabilities",
    )
    commands = parser.add_subparsers(dest="evolution_command", required=True)

    status = commands.add_parser("status", help="Show the read-only evolution projection")
    _state_arg(status)
    status.set_defaults(func=_status)

    attempt = commands.add_parser("attempt", help="Materialize an evolution attempt")
    _state_arg(attempt)
    attempt.add_argument("--file", required=True)
    attempt.set_defaults(func=_attempt)

    evaluator = commands.add_parser(
        "evaluator-register",
        help="Register public evaluator metadata and sealed cases",
    )
    _state_arg(evaluator)
    evaluator.add_argument("--public-file", required=True)
    evaluator.add_argument("--sealed-cases-file", required=True)
    evaluator.add_argument("--sealed-root", required=True)
    evaluator.add_argument("--access-token-env", default="ZF_EVOLUTION_EVALUATOR_TOKEN")
    evaluator.set_defaults(func=_evaluator_register)

    ensure = commands.add_parser("trial-ensure", help="Ensure a stable A/B trial row")
    _state_arg(ensure)
    ensure.add_argument("--attempt-id", required=True)
    ensure.add_argument("--arm", choices=("baseline", "candidate"), required=True)
    ensure.add_argument("--replicate", type=int, required=True)
    ensure.set_defaults(func=_trial_ensure)

    start = commands.add_parser("trial-start", help="Claim a trial lease")
    _state_arg(start)
    start.add_argument("--trial-id", required=True)
    start.add_argument("--lease-owner", required=True)
    start.add_argument("--lease-expires-at", required=True)
    start.set_defaults(func=_trial_start)

    settle = commands.add_parser("trial-settle", help="Settle one trial attempt")
    _state_arg(settle)
    settle.add_argument("--trial-id", required=True)
    settle.add_argument("--lease-owner", required=True)
    settle.add_argument("--attempt-number", type=int, required=True)
    settle.add_argument(
        "--outcome",
        choices=("passed", "semantic_failed", "infrastructure_failed"),
        required=True,
    )
    settle.add_argument("--evaluator-file")
    settle.add_argument("--measurement-file")
    settle.add_argument("--archive-ref", required=True)
    settle.add_argument("--archive-digest", required=True)
    settle.add_argument("--cost-receipt-ref", action="append", default=[])
    settle.add_argument("--failure-class", default="")
    settle.set_defaults(func=_trial_settle)

    execute = commands.add_parser(
        "trial-execute",
        help="Execute one resident-owned evolution trial/canary request",
    )
    _state_arg(execute)
    execute.add_argument("--request-event-id", required=True)
    execute.set_defaults(func=_trial_execute)

    compare = commands.add_parser("compare", help="Compare settled repeated A/B trials")
    _state_arg(compare)
    compare.add_argument("--attempt-id", required=True)
    compare.add_argument("--evaluator-file", required=True)
    compare.set_defaults(func=_compare)

    propose = commands.add_parser("asset-propose", help="Propose a learning asset")
    _state_arg(propose)
    propose.add_argument("--file", required=True)
    propose.add_argument("--comparison-id", required=True)
    propose.set_defaults(func=_asset_propose)

    transition = commands.add_parser(
        "asset-transition",
        help="Record an externally controlled asset lifecycle receipt",
    )
    _state_arg(transition)
    transition.add_argument("--asset-id", required=True)
    transition.add_argument("--version", type=int, required=True)
    transition.add_argument("--target-state", required=True)
    transition.add_argument("--expected-revision", type=int, required=True)
    transition.add_argument("--action-id", required=True)
    transition.add_argument("--receipt-ref-file", required=True)
    transition.set_defaults(func=_asset_transition)

    outcome = commands.add_parser(
        "asset-outcome",
        help="Record one idempotent learning-asset usage outcome",
    )
    _state_arg(outcome)
    outcome.add_argument("--asset-id", required=True)
    outcome.add_argument("--version", type=int, required=True)
    outcome.add_argument("--usage-ref", required=True)
    outcome.add_argument("--matched", action=argparse.BooleanOptionalAction, default=True)
    outcome.add_argument(
        "--outcome",
        choices=("passed", "failed", "regressed", "neutral"),
        required=True,
    )
    outcome.add_argument("--cost-file")
    outcome.add_argument("--cohort-file")
    outcome.add_argument("--evaluation-file")
    outcome.set_defaults(func=_asset_outcome)

    skill_outcome = commands.add_parser(
        "skill-outcome",
        help="Credit a skill only when current-dispatch invocation is observed",
    )
    _state_arg(skill_outcome)
    skill_outcome.add_argument("--asset-id", required=True)
    skill_outcome.add_argument("--version", type=int, required=True)
    skill_outcome.add_argument("--skill", required=True)
    skill_outcome.add_argument("--task", required=True)
    skill_outcome.add_argument("--role", required=True)
    skill_outcome.add_argument(
        "--outcome",
        choices=("passed", "failed", "regressed", "neutral"),
        required=True,
    )
    skill_outcome.add_argument("--cost-file")
    skill_outcome.set_defaults(func=_skill_outcome)

    export = commands.add_parser("asset-export", help="Export a retained asset")
    _state_arg(export)
    export.add_argument("--asset-id", required=True)
    export.add_argument("--version", type=int, required=True)
    export.set_defaults(func=_asset_export)

    import_parser = commands.add_parser(
        "asset-import",
        help="Import a portable asset as an inactive target-validation candidate",
    )
    _state_arg(import_parser)
    import_parser.add_argument("--package-ref-file", required=True)
    import_parser.add_argument("--source-state-dir")
    import_parser.add_argument("--target-project", required=True)
    import_parser.set_defaults(func=_asset_import)

    target_validation = commands.add_parser(
        "asset-target-validate",
        help="Record controlled target-project validation for an imported asset",
    )
    _state_arg(target_validation)
    target_validation.add_argument("--asset-id", required=True)
    target_validation.add_argument("--version", type=int, required=True)
    target_validation.add_argument("--expected-revision", type=int, required=True)
    target_validation.add_argument("--action-id", required=True)
    target_validation.add_argument(
        "--passed", action=argparse.BooleanOptionalAction, required=True
    )
    target_validation.add_argument("--receipt-ref-file", required=True)
    target_validation.set_defaults(func=_asset_target_validate)

    variant = commands.add_parser(
        "variant-compare",
        help="Materialize a Pareto comparison for workflow/provider variants",
    )
    _state_arg(variant)
    variant.add_argument("--variants-file", required=True)
    variant.add_argument("--dimensions-file", required=True)
    variant.set_defaults(func=_variant_compare)

    current = commands.add_parser(
        "variant-current",
        help="Check provider comparison fingerprints against current routes",
    )
    current.add_argument("--comparison-file", required=True)
    current.add_argument("--fingerprints-file", required=True)
    current.set_defaults(func=_variant_current)

    economics = commands.add_parser(
        "economics",
        help="Compute evidence-bound evolution economics without inventing values",
    )
    economics.add_argument("--candidate-generation-file", required=True)
    economics.add_argument("--evaluation-file", required=True)
    economics.set_defaults(func=_economics)

    opportunity = commands.add_parser(
        "opportunity-propose",
        help="Materialize a proposal-only evolution opportunity",
    )
    _state_arg(opportunity)
    opportunity.add_argument("--file", required=True)
    opportunity.set_defaults(func=_opportunity_propose)

    challenge = commands.add_parser(
        "challenge-materialize",
        help="Materialize a visible shadow challenge candidate",
    )
    _state_arg(challenge)
    challenge.add_argument("--file", required=True)
    challenge.set_defaults(func=_challenge_materialize)

    challenge_decide = commands.add_parser(
        "challenge-decide",
        help="Promote or reject a stable shadow challenge with evaluator receipt",
    )
    _state_arg(challenge_decide)
    challenge_decide.add_argument("--challenge-id", required=True)
    challenge_decide.add_argument("--expected-revision", type=int, required=True)
    challenge_decide.add_argument("--verdict", choices=("promoted", "rejected"), required=True)
    challenge_decide.add_argument("--receipt-ref-file", required=True)
    challenge_decide.set_defaults(func=_challenge_decide)

    workflow = commands.add_parser(
        "workflow-learning-propose",
        help="Compile Loop Learning into a standard Workflow Proposal",
    )
    _state_arg(workflow)
    workflow.add_argument("--promotion-ref-file", required=True)
    workflow.add_argument("--request-file", required=True)
    workflow.add_argument("--base-config", required=True)
    workflow.add_argument("--candidate-config", required=True)
    workflow.add_argument("--preflight-file", required=True)
    workflow.set_defaults(func=_workflow_learning_propose)


def _state_arg(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--state-dir")


def _context(args: argparse.Namespace):
    return resolve_project_context(
        explicit_state_dir=getattr(args, "state_dir", None),
        load_config_with_explicit=True,
    )


def _coordinator(args: argparse.Namespace) -> EvolutionCoordinator:
    return EvolutionCoordinator(_context(args).state_dir)


def _status(args: argparse.Namespace) -> int:
    _print(_coordinator(args).projection())
    return 0


def _attempt(args: argparse.Namespace) -> int:
    _print(_coordinator(args).materialize_attempt(_load_json(args.file)))
    return 0


def _evaluator_register(args: argparse.Namespace) -> int:
    token = os.environ.get(args.access_token_env, "")
    authority = SealedEvaluatorAuthority(Path(args.sealed_root), access_token=token)
    cases = _load_json(args.sealed_cases_file)
    if not isinstance(cases, list):
        raise ValueError("sealed cases file must contain a JSON list")
    public, descriptor = authority.register_generation(
        state_dir=_context(args).state_dir,
        public_spec=_load_json(args.public_file),
        sealed_cases=cases,
    )
    context = _context(args)
    EventWriter(event_log_from_project(context.state_dir, config=context.config)).emit(
        "evolution.evaluator.registered",
        actor="zf-cli",
        correlation_id=str(public["generation_id"]),
        payload={
            "generation_id": public["generation_id"],
            "generation_digest": public["generation_digest"],
            "holdout_authority_ref": public["holdout_authority_ref"],
            "holdout_generation_digest": public["holdout_generation_digest"],
            "artifact_ref": descriptor,
        },
    )
    _print({"generation": public, "artifact_ref": descriptor})
    return 0


def _trial_ensure(args: argparse.Namespace) -> int:
    _print(_coordinator(args).ensure_trial(
        attempt_id=args.attempt_id,
        arm=args.arm,
        replicate=args.replicate,
    ))
    return 0


def _trial_start(args: argparse.Namespace) -> int:
    _print(_coordinator(args).start_trial(
        args.trial_id,
        lease_owner=args.lease_owner,
        lease_expires_at=args.lease_expires_at,
    ))
    return 0


def _trial_settle(args: argparse.Namespace) -> int:
    _print(_coordinator(args).settle_trial(
        args.trial_id,
        lease_owner=args.lease_owner,
        attempt_number=args.attempt_number,
        outcome=args.outcome,
        evaluator_generation=(
            _load_json(args.evaluator_file) if args.evaluator_file else None
        ),
        measurement=(
            _load_json(args.measurement_file) if args.measurement_file else None
        ),
        archive_ref=args.archive_ref,
        archive_digest=args.archive_digest,
        cost_receipt_refs=list(args.cost_receipt_ref),
        failure_class=args.failure_class,
    ))
    return 0


def _trial_execute(args: argparse.Namespace) -> int:
    from zf.runtime.evolution_trial_runner import execute_evolution_request

    context = _context(args)
    writer = EventWriter(
        event_log_from_project(context.state_dir, config=context.config)
    )
    result = execute_evolution_request(
        state_dir=context.state_dir,
        project_root=context.project_root,
        config=context.config,
        request_event_id=args.request_event_id,
        writer=writer,
    )
    _print(result)
    return 0 if bool(result.get("ok")) else 1


def _compare(args: argparse.Namespace) -> int:
    _print(_coordinator(args).compare_attempt(
        args.attempt_id,
        evaluator_generation=_load_json(args.evaluator_file),
    ))
    return 0


def _asset_propose(args: argparse.Namespace) -> int:
    _print(_coordinator(args).propose_asset(
        _load_json(args.file),
        comparison_id=args.comparison_id,
    ))
    return 0


def _asset_transition(args: argparse.Namespace) -> int:
    _print(_coordinator(args).transition_asset(
        asset_id=args.asset_id,
        version=args.version,
        target_state=args.target_state,
        expected_revision=args.expected_revision,
        action_id=args.action_id,
        receipt_ref=_load_json(args.receipt_ref_file),
    ))
    return 0


def _asset_outcome(args: argparse.Namespace) -> int:
    _print(_coordinator(args).record_asset_outcome(
        asset_id=args.asset_id,
        version=args.version,
        usage_ref=args.usage_ref,
        matched=bool(args.matched),
        outcome=args.outcome,
        cost=_load_json(args.cost_file) if args.cost_file else {},
        cohort=_load_json(args.cohort_file) if args.cohort_file else {},
        evaluation=(
            _load_json(args.evaluation_file) if args.evaluation_file else {}
        ),
    ))
    return 0


def _skill_outcome(args: argparse.Namespace) -> int:
    context = _context(args)
    _print(_coordinator(args).record_skill_outcome(
        asset_id=args.asset_id,
        version=args.version,
        skill_name=args.skill,
        task_id=args.task,
        role_instance=args.role,
        outcome=args.outcome,
        cost=_load_json(args.cost_file) if args.cost_file else {},
        config=context.config,
        project_root=context.project_root,
    ))
    return 0


def _asset_export(args: argparse.Namespace) -> int:
    _print(_coordinator(args).export_asset(
        asset_id=args.asset_id,
        version=args.version,
    ))
    return 0


def _asset_import(args: argparse.Namespace) -> int:
    _print(_coordinator(args).import_asset(
        package_descriptor=_load_json(args.package_ref_file),
        target_project=args.target_project,
        source_state_dir=(
            Path(args.source_state_dir) if args.source_state_dir else None
        ),
    ))
    return 0


def _asset_target_validate(args: argparse.Namespace) -> int:
    _print(_coordinator(args).record_target_validation(
        asset_id=args.asset_id,
        version=args.version,
        expected_revision=args.expected_revision,
        action_id=args.action_id,
        passed=bool(args.passed),
        receipt_ref=_load_json(args.receipt_ref_file),
    ))
    return 0


def _variant_compare(args: argparse.Namespace) -> int:
    variants = _load_json(args.variants_file)
    dimensions = _load_json(args.dimensions_file)
    if not isinstance(variants, list) or not isinstance(dimensions, dict):
        raise ValueError("variant comparison requires a list and dimensions object")
    _print(_coordinator(args).materialize_variant_comparison(
        variants=variants,
        dimensions=dimensions,
    ))
    return 0


def _variant_current(args: argparse.Namespace) -> int:
    from zf.runtime.evolution_learning import provider_comparison_is_current

    comparison = _load_json(args.comparison_file)
    fingerprints = _load_json(args.fingerprints_file)
    if not isinstance(comparison, dict) or not isinstance(fingerprints, dict):
        raise ValueError("variant currentness requires JSON objects")
    current, reason = provider_comparison_is_current(
        comparison,
        current_fingerprints={
            str(key): str(value) for key, value in fingerprints.items()
        },
    )
    _print({"current": current, "reason": reason})
    return 0


def _economics(args: argparse.Namespace) -> int:
    from zf.runtime.evolution_learning import evolution_economics

    candidate = _load_json(args.candidate_generation_file)
    evaluation = _load_json(args.evaluation_file)
    if not isinstance(candidate, dict) or not isinstance(evaluation, dict):
        raise ValueError("evolution economics requires JSON objects")
    _print(evolution_economics(
        candidate_generation=candidate,
        evaluation=evaluation,
    ))
    return 0


def _opportunity_propose(args: argparse.Namespace) -> int:
    _print(_coordinator(args).materialize_opportunity(_load_json(args.file)))
    return 0


def _challenge_materialize(args: argparse.Namespace) -> int:
    _print(_coordinator(args).materialize_challenge(_load_json(args.file)))
    return 0


def _challenge_decide(args: argparse.Namespace) -> int:
    _print(_coordinator(args).decide_challenge(
        challenge_id=args.challenge_id,
        expected_revision=args.expected_revision,
        verdict=args.verdict,
        evaluator_receipt_ref=_load_json(args.receipt_ref_file),
    ))
    return 0


def _workflow_learning_propose(args: argparse.Namespace) -> int:
    from zf.runtime.evolution_learning import compile_workflow_learning_proposal

    context = _context(args)
    proposal, descriptor = compile_workflow_learning_proposal(
        context.state_dir,
        promotion_descriptor=_load_json(args.promotion_ref_file),
        request=_load_json(args.request_file),
        base_config_path=Path(args.base_config),
        candidate_config_path=Path(args.candidate_config),
        preflight=_load_json(args.preflight_file),
        writer=EventWriter(
            event_log_from_project(context.state_dir, config=context.config)
        ),
    )
    _print({"proposal": proposal, "artifact_ref": descriptor})
    return 0


def _load_json(path: str | Path) -> Any:
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(value, (dict, list)):
        raise ValueError(f"JSON input must be an object or list: {path}")
    return value


def _print(value: Any) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True))


__all__ = ["register"]
