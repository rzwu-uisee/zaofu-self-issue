"""Signal-aware command loop for the Autoresearch resident."""

from __future__ import annotations

import signal
import threading
from pathlib import Path
from typing import Any

from zf.autoresearch.resident import actions_json, run_resident_once


def run_resident_cli(args: Any, *, state_dir: Path) -> int:
    output_root = (
        args.output_root
        if args.output_root is not None
        else state_dir / "autoresearch" / "resident"
    )
    interval = max(float(getattr(args, "interval_seconds", 10.0) or 10.0), 0.1)
    max_actions_per_tick = max(
        int(getattr(args, "max_actions_per_tick", 0) or 0),
        0,
    )
    shutdown_requested = threading.Event()

    def _request_shutdown(_signum, _frame) -> None:
        shutdown_requested.set()

    previous_sigterm = signal.signal(signal.SIGTERM, _request_shutdown)
    try:
        while not shutdown_requested.is_set():
            actions = run_resident_once(
                state_dir=state_dir,
                worktree_root=args.worktree_root,
                output_root=output_root,
                execute=args.execute,
                self_repair_consumer=args.self_repair_consumer,
                self_repair_spawn=args.self_repair_spawn,
                self_repair_backend=args.self_repair_backend,
                max_actions_per_tick=max_actions_per_tick,
                should_stop=shutdown_requested.is_set,
            )
            print(actions_json(actions), flush=True)
            if shutdown_requested.is_set() or not getattr(args, "watch", False):
                break
            shutdown_requested.wait(interval)
    finally:
        signal.signal(signal.SIGTERM, previous_sigterm)
    return 0


__all__ = ["run_resident_cli"]
