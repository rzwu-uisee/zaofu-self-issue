"""Seed an isolated Channel product-contract browser fixture."""

from __future__ import annotations

import argparse
from pathlib import Path

from zf.core.events import EventWriter
from zf.core.events.log import EventLog
from zf.runtime.control_actions import ControlledActionService


CHANNEL_ID = "ch-product-contract-e2e"
THREAD_ID = "main"
PRD_REF = f"channels/{CHANNEL_ID}/prd/r1.json"
PRD_DIGEST = "a" * 64


def _execute(
    service: ControlledActionService,
    writer: EventWriter,
    action: str,
    payload: dict,
) -> dict:
    requested = writer.emit(
        "web.action.requested",
        actor="web",
        payload={"action": action, "request": payload},
    )
    result = service.execute(
        action=action,
        requested_action=action,
        payload=payload,
        requested=requested,
    )
    if not result.get("ok"):
        raise RuntimeError(f"{action} failed: {result}")
    return result


def seed(state_dir: Path) -> None:
    state_dir.mkdir(parents=True, exist_ok=True)
    writer = EventWriter(EventLog(state_dir / "events.jsonl"))
    service = ControlledActionService(
        state_dir,
        writer,
        actor="web",
        source="channel-product-e2e",
        surface="web",
        project_root=state_dir.parent,
    )
    _execute(
        service,
        writer,
        "channel-create",
        {
            "channel_id": CHANNEL_ID,
            "name": "Product contract E2E",
            "owner_actor_ref": "web",
        },
    )
    _execute(
        service,
        writer,
        "channel-invite-member",
        {
            "channel_id": CHANNEL_ID,
            "member_id": "product-pm",
            "provider": "fake",
            "backend": "fake",
            "channel_role": "product_pm",
            "permission_profile": "read_only",
            "permissions": [
                "read",
                "message",
                "summarize",
                "propose_workflow",
            ],
        },
    )
    _execute(
        service,
        writer,
        "channel-set-leader",
        {
            "channel_id": CHANNEL_ID,
            "leader_member_id": "product-pm",
            "expected_revision": 0,
            "idempotency_key": "channel-product-e2e-leader-r1",
        },
    )
    _execute(
        service,
        writer,
        "channel-discussion-mode",
        {
            "channel_id": CHANNEL_ID,
            "thread_id": THREAD_ID,
            "mode": "conversation",
            "default_responder_id": "product-pm",
            "max_rounds": 6,
        },
    )
    _execute(
        service,
        writer,
        "channel-post-message",
        {
            "channel_id": CHANNEL_ID,
            "thread_id": THREAD_ID,
            "client_message_id": "channel-product-e2e-seed-user",
            "member_id": "operator",
            "role": "user",
            "text": "Define a durable Channel product contract.",
        },
    )
    _execute(
        service,
        writer,
        "channel-post-message",
        {
            "channel_id": CHANNEL_ID,
            "thread_id": THREAD_ID,
            "client_message_id": "channel-product-e2e-seed-assistant",
            "member_id": "product-pm",
            "role": "assistant",
            "text": "The canonical PRD revision is ready for Owner review.",
        },
    )
    writer.emit(
        "channel.discussion.started",
        actor="product-pm",
        correlation_id=CHANNEL_ID,
        payload={
            "channel_id": CHANNEL_ID,
            "thread_id": THREAD_ID,
            "discussion_id": "discussion-product-e2e",
            "mode": "conversation",
            "product_mode": "conversation",
            "requirement_message_id": "channel-product-e2e-seed-user",
            "state": "collecting",
            "source": "channel-product-e2e",
        },
    )
    writer.emit(
        "channel.consensus.proposed",
        actor="product-pm",
        correlation_id=CHANNEL_ID,
        payload={
            "channel_id": CHANNEL_ID,
            "thread_id": THREAD_ID,
            "artifact_ref": PRD_REF,
            "artifact_digest": PRD_DIGEST,
            "prd_ref": PRD_REF,
            "prd_digest": PRD_DIGEST,
            "prd_revision": 1,
            "readiness_verdict": "ready",
            "owner_actor_ref": "web",
            "proposed_by": "product-pm",
            "product_mode": "conversation",
            "source": "channel-product-e2e",
        },
    )
    writer.emit(
        "channel.result.receipt.recorded",
        actor="zf-channel-result-reconciler",
        correlation_id=CHANNEL_ID,
        payload={
            "schema_version": "channel-result-receipt.v1",
            "channel_id": CHANNEL_ID,
            "thread_id": THREAD_ID,
            "receipt_id": "receipt-product-e2e",
            "receipt_kind": "workflow_terminal",
            "status": "passed",
            "source_event_id": "evt-product-e2e-terminal",
            "source_event_type": "judge.passed",
            "workflow_run_id": "run-product-e2e",
            "task_id": "TASK-PRODUCT-E2E",
            "artifact_ref": "artifacts/product-e2e/result.json",
            "artifact_digest": "b" * 64,
            "receipt_ref": (
                f"channels/{CHANNEL_ID}/receipts/"
                "receipt-product-e2e.json"
            ),
            "receipt_digest": "c" * 64,
            "origin_binding": {
                "surface": "channel",
                "channel_id": CHANNEL_ID,
                "thread_id": THREAD_ID,
            },
            "source": "channel-product-e2e",
        },
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--state-dir", type=Path, required=True)
    args = parser.parse_args()
    seed(args.state_dir.resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
