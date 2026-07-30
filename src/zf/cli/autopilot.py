"""zf autopilot — deterministic proposal-only runner."""

from __future__ import annotations

import argparse
import json
import sys

from zf.core.config.loader import ConfigError
from zf.core.config.project_context import resolve_project_context
from zf.runtime.autopilot import run_autopilot_tick


def register(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser(
        "autopilot",
        help="Run deterministic Autopilot proposal checks",
    )
    sub = parser.add_subparsers(dest="autopilot_cmd")

    tick = sub.add_parser("tick", help="Scan runtime state and create proposals")
    tick.add_argument(
        "--state-dir",
        type=str,
        default=None,
        help="Path to runtime state dir (default: project.state_dir from zf.yaml)",
    )
    tick.add_argument(
        "--dry-run",
        action="store_true",
        help="Show proposals without writing events.jsonl",
    )
    tick.add_argument(
        "--json",
        action="store_true",
        help="Print machine-readable JSON",
    )
    tick.set_defaults(func=run_tick)
    parser.set_defaults(func=run_help)


def run_help(args: argparse.Namespace) -> int:
    print("Usage: zf autopilot tick [--dry-run] [--json] [--state-dir PATH]")
    return 0


def run_tick(args: argparse.Namespace) -> int:
    try:
        context = resolve_project_context(
            explicit_state_dir=getattr(args, "state_dir", None),
            require_config=True,
            load_config_with_explicit=True,
        )
    except ConfigError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    if not context.state_dir.exists():
        print(
            "Error: runtime state directory does not exist; run `zf init` first.",
            file=sys.stderr,
        )
        return 1

    result = run_autopilot_tick(
        context.state_dir,
        config=context.config,
        dry_run=bool(getattr(args, "dry_run", False)),
    )
    if getattr(args, "json", False):
        print(json.dumps({
            "enabled": result.enabled,
            "mode": result.mode,
            "dry_run": result.dry_run,
            "created_count": result.created_count,
            "skipped_duplicates": result.skipped_duplicates,
            "proposals": [proposal.payload() for proposal in result.created],
        }, ensure_ascii=False, indent=2))
        return 0

    if not result.enabled:
        print("Autopilot is disabled: zf.yaml autopilot.enabled=false")
        return 0

    action = "would create" if result.dry_run else "created"
    print(
        f"Autopilot tick complete: {action} {result.created_count} proposal(s); "
        f"skipped {result.skipped_duplicates} duplicate(s)."
    )
    for proposal in result.created:
        payload = proposal.payload()
        print(
            f"- {payload['proposal_id']} {payload['kind']} "
            f"{payload.get('task_id') or 'project'}: {payload['reason']}"
        )
    return 0
