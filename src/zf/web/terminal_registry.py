"""Atomic sidecar registry for Web terminal resource identity."""

from __future__ import annotations

import json
from pathlib import Path
from threading import RLock
from typing import Callable, TypeVar

from zf.core.safety.path_guard import PathGuard
from zf.core.state.atomic_io import atomic_write_text
from zf.core.state.locks import locked_path


REGISTRY_SCHEMA = "terminal-resource-registry.v1"
REGISTRY_FILENAME = "terminal-resource-registry.v1.json"
T = TypeVar("T")


class TerminalRegistryError(RuntimeError):
    pass


class TerminalRegistry:
    def __init__(self, state_dir: Path, *, project_id: str, project_root: Path) -> None:
        self.state_dir = Path(state_dir).resolve(strict=False)
        self.project_id = project_id
        self.project_root = Path(project_root).resolve(strict=False)
        self.path = PathGuard.assert_under(
            self.state_dir / REGISTRY_FILENAME,
            self.state_dir,
        )
        self._thread_lock = RLock()

    def _empty(self) -> dict[str, object]:
        return {
            "schema_version": REGISTRY_SCHEMA,
            "revision": 0,
            "project_id": self.project_id,
            "project_root": str(self.project_root),
            "runtime": {
                "backend": "herdr",
                "herdr_session": "",
                "workspace_id": "",
                "server_pid": None,
            },
            "sessions": [],
            "receipts": [],
        }

    def _read_unlocked(self) -> dict[str, object]:
        if not self.path.exists():
            return self._empty()
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise TerminalRegistryError(f"invalid terminal registry: {exc}") from exc
        if not isinstance(value, dict) or value.get("schema_version") != REGISTRY_SCHEMA:
            raise TerminalRegistryError("unsupported terminal registry schema")
        if value.get("project_id") != self.project_id:
            raise TerminalRegistryError("terminal registry project_id mismatch")
        if Path(str(value.get("project_root") or "")).resolve(strict=False) != self.project_root:
            raise TerminalRegistryError("terminal registry project_root mismatch")
        if not isinstance(value.get("runtime"), dict) or not isinstance(
            value.get("sessions"), list
        ):
            raise TerminalRegistryError("invalid terminal registry shape")
        if "receipts" in value and not isinstance(value.get("receipts"), list):
            raise TerminalRegistryError("terminal registry receipts must be a list")
        return value

    def _write_unlocked(self, value: dict[str, object]) -> None:
        value["schema_version"] = REGISTRY_SCHEMA
        value["revision"] = int(value.get("revision") or 0) + 1
        atomic_write_text(
            self.path,
            json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        )

    def read(self) -> dict[str, object]:
        with self._thread_lock, locked_path(self.path):
            return self._read_unlocked()

    def mutate(self, fn: Callable[[dict[str, object]], T]) -> T:
        """Run one read/side-effect/write transaction under the project lock.

        The callback may call Herdr.  Holding the lock across that bounded call
        makes concurrent Web workers converge on one slot/resource creation.
        """

        with self._thread_lock, locked_path(self.path):
            value = self._read_unlocked()
            result = fn(value)
            self._write_unlocked(value)
            return result
