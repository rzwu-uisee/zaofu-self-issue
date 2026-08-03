"""Task liveness guards backed by admitted writer completion evidence."""

from __future__ import annotations


class TaskCompletionLivenessMixin:
    def _task_has_admitted_writer_completion(self, task_id: str) -> bool:
        """Return true when the latest writer dispatch already settled."""

        try:
            events = self.event_log.read_all()
        except Exception:
            return False
        latest_dispatch = -1
        latest_completion = -1
        for index, event in enumerate(events):
            payload = event.payload if isinstance(event.payload, dict) else {}
            event_task_id = str(event.task_id or payload.get("task_id") or "")
            if event_task_id != task_id:
                continue
            if event.type == "fanout.child.dispatched":
                latest_dispatch = index
                latest_completion = -1
                continue
            if index <= latest_dispatch or event.type != "fanout.child.completed":
                continue
            admitted_ref = payload.get("admitted_call_result_ref")
            if (
                isinstance(admitted_ref, dict)
                and str(admitted_ref.get("ref") or "").strip()
                and str(admitted_ref.get("sha256") or "").strip()
            ):
                latest_completion = index
        return latest_dispatch >= 0 and latest_completion > latest_dispatch
