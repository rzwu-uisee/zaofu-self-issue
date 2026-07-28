"""Durable EventLog cursor ownership for the orchestrator."""

from __future__ import annotations


class EventCursorMixin:
    """Persist only EventLog boundaries that the runtime has consumed."""

    def _load_offset(self) -> int:
        try:
            return self.session_store.load().latest_event_offset
        except Exception:
            return 0

    def _persist_offset(self, offset: int) -> None:
        try:
            self.session_store.update(latest_event_offset=offset)
        except Exception:
            pass

    def acknowledge_consumed_offset(self, consumed_offset: int) -> None:
        """Persist an EventWatcher boundary after its batch was fully handled."""
        if consumed_offset < 0:
            raise ValueError("consumed event offset must be non-negative")
        if consumed_offset > self._load_offset():
            self._persist_offset(consumed_offset)


__all__ = ["EventCursorMixin"]
