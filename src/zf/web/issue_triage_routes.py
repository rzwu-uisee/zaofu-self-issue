"""Read-only GitHub Issue Triage mirror routes and optional webhook ingress."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import os
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, Header, Query, Request
from fastapi.responses import JSONResponse, Response

from zf.core.config.schema import ZfConfig
from zf.core.events.factory import event_log_from_project
from zf.core.events.model import ZfEvent
from zf.core.issue_triage.models import IssueMirror
from zf.core.issue_triage.store import IssueMirrorStore
from zf.integrations.forge.github_issues import (
    GitHubIssueReconciler,
    normalize_github_issue,
)
from zf.runtime.external_issue_ingress import ExternalIssueIngressService

ReconcilerFactory = Callable[[Any, str], GitHubIssueReconciler]

_GITHUB_IMAGE_HOSTS = {
    "github.com",
    "user-images.githubusercontent.com",
    "camo.githubusercontent.com",
}
_MAX_GITHUB_IMAGE_BYTES = 20_000_000


def _is_github_image_url(value: str) -> bool:
    try:
        parsed = urllib.parse.urlparse(value)
        port = parsed.port
    except ValueError:
        return False
    if (
        parsed.scheme != "https"
        or parsed.hostname not in _GITHUB_IMAGE_HOSTS
        or port not in {None, 443}
        or parsed.username is not None
        or parsed.password is not None
    ):
        return False
    return parsed.hostname != "github.com" or parsed.path.startswith("/user-attachments/")


def _read_github_image(value: str) -> tuple[bytes, str]:
    request = urllib.request.Request(
        value,
        headers={"Accept": "image/*", "User-Agent": "ZaoFu-Issue-Triage"},
    )
    with urllib.request.urlopen(request, timeout=20) as response:  # noqa: S310
        content_type = str(response.headers.get("Content-Type") or "").split(";", 1)[0]
        if not content_type.startswith("image/"):
            raise ValueError("GitHub attachment is not an image")
        raw = response.read(_MAX_GITHUB_IMAGE_BYTES + 1)
    if len(raw) > _MAX_GITHUB_IMAGE_BYTES:
        raise ValueError("GitHub image exceeds the 20 MB safe limit")
    return raw, content_type


def _repository(config: ZfConfig | None) -> str:
    if config is None or not config.self_issue.enabled:
        return ""
    target = config.self_issue.targets.get("github")
    if target is not None:
        return target.project
    if config.self_issue.provider == "github":
        return config.self_issue.target_project
    return ""


def _public(
    item: IssueMirror,
    *,
    workflow: dict[str, Any] | None = None,
) -> dict[str, Any]:
    value = item.to_dict()
    if workflow is not None:
        value["workflow"] = workflow
    return value


def _workflow_projection(
    events: list[ZfEvent],
    item: IssueMirror,
) -> dict[str, Any] | None:
    state = "mirrored"
    task_id = ""
    source_revision = ""
    proposal_id = ""
    run_id = ""
    seen = False
    for event in events:
        payload = event.payload if isinstance(event.payload, dict) else {}
        event_source_key = str(payload.get("source_key") or "")
        same_source = event_source_key == item.issue_key
        same_task = bool(task_id and event.task_id == task_id)
        if event.type == "external_issue.received" and same_source:
            seen = True
            state = "mirrored"
            source_revision = str(payload.get("source_revision") or "")
        elif event.type == "external_issue.triage.queued" and same_source:
            seen = True
            state = "triage_queued"
            task_id = str(event.task_id or "")
            source_revision = str(payload.get("source_revision") or source_revision)
            run_id = str(payload.get("workflow_run_id") or "")
        elif event.type == "workflow.invoke.accepted" and same_task:
            event_run = str(
                payload.get("workflow_run_id") or event.correlation_id or ""
            )
            if not run_id or event_run == run_id:
                state = "triaging"
                run_id = event_run or run_id
        elif event.type == "operator.action.proposed" and same_task:
            proposal = payload.get("proposal")
            proposal = proposal if isinstance(proposal, dict) else {}
            proposal_payload = proposal.get("payload")
            proposal_payload = (
                proposal_payload if isinstance(proposal_payload, dict) else {}
            )
            if str(proposal_payload.get("route_id") or "").startswith(
                "delivery:issue:"
            ):
                state = "awaiting_fix_approval"
                proposal_id = str(proposal.get("proposal_id") or "")
        elif event.type == "workflow.invoke.requested" and same_task:
            if str(payload.get("flow_kind") or "") == "issue":
                state = "fix_queued"
                run_id = str(payload.get("workflow_run_id") or run_id)
        elif (
            event.type in {"task.dispatched", "fanout.child.dispatched"}
            and same_task
            and state in {"fix_queued", "fixing"}
        ):
            state = "fixing"
        elif event.type == "candidate.ready" and same_task:
            state = "verifying"
        elif event.type == "judge.passed" and same_task:
            state = "verified_candidate"
        elif event.type.endswith(".blocked") and same_task:
            state = "blocked"
        elif event.type.endswith(".failed") and same_task:
            state = "failed"
    if not seen:
        return None
    return {
        "state": state,
        "task_id": task_id,
        "source_revision": source_revision,
        "proposal_id": proposal_id,
        "run_id": run_id,
    }


def build_issue_triage_router(
    *,
    resolve_ctx: Callable[[str], Any],
    workspace_config: ZfConfig | None = None,
    reconciler_factory: ReconcilerFactory | None = None,
    webhook_secret: Callable[[], str] | None = None,
) -> APIRouter:
    router = APIRouter()
    build_reconciler = reconciler_factory or (
        lambda state_dir, repository: GitHubIssueReconciler(state_dir, repository)
    )
    read_webhook_secret = webhook_secret or (
        lambda: os.environ.get("ZF_GITHUB_TRIAGE_WEBHOOK_SECRET", "").strip()
    )

    def context(project_id: str) -> tuple[Any, str, IssueMirrorStore]:
        ctx = resolve_ctx(project_id)
        repository = _repository(ctx.config) or _repository(workspace_config)
        if not repository:
            raise ValueError("The centrally managed GitHub Self-Issue target is not configured")
        return ctx, repository, IssueMirrorStore(ctx.state_dir)

    @router.get("/api/projects/{project_id}/issue-triage/summary")
    def issue_triage_summary(project_id: str) -> JSONResponse:
        try:
            _, repository, store = context(project_id)
            items = store.list()
            sync = store.sync_state()
        except ValueError as exc:
            return JSONResponse({"ok": False, "status": "unconfigured", "reason": str(exc)}, status_code=409)
        groups = Counter(item.derived_group for item in items)
        states = Counter(item.github_state for item in items)
        labels = Counter(label for item in items for label in item.labels)
        label_colors = {
            name: color
            for item in items
            for name, color in item.label_colors.items()
        }
        authors = Counter(item.author_login for item in items)
        author_states = {
            login: {
                "open": sum(1 for item in items if item.author_login == login and item.github_state == "open"),
                "closed": sum(1 for item in items if item.author_login == login and item.github_state == "closed"),
            }
            for login in authors
        }
        return JSONResponse({
            "schema_version": "issue-triage-summary.v1",
            "repository": repository,
            "repository_url": f"https://github.com/{repository}",
            "new_issue_url": f"https://github.com/{repository}/issues/new",
            "total": len(items),
            "groups": dict(groups),
            "states": dict(states),
            "labels": dict(sorted(labels.items())),
            "label_colors": dict(sorted(label_colors.items())),
            "authors": dict(sorted(authors.items())),
            "author_states": dict(sorted(author_states.items())),
            "sync": sync.to_dict(),
        })

    @router.get("/api/projects/{project_id}/issue-triage/attachment")
    def issue_triage_attachment(project_id: str, url: str) -> Response:
        try:
            context(project_id)
        except ValueError as exc:
            return JSONResponse({"ok": False, "status": "unconfigured", "reason": str(exc)}, status_code=409)
        if not _is_github_image_url(url):
            return JSONResponse({"ok": False, "status": "invalid_attachment_url"}, status_code=400)
        try:
            raw, content_type = _read_github_image(url)
        except (OSError, ValueError) as exc:
            return JSONResponse({
                "ok": False,
                "status": "attachment_unavailable",
                "reason": str(exc),
            }, status_code=502)
        return Response(
            content=raw,
            media_type=content_type,
            headers={"Cache-Control": "private, max-age=300"},
        )

    @router.get("/api/projects/{project_id}/issue-triage")
    def issue_triage_list(
        project_id: str,
        q: str = "",
        group: str = "",
        state: str = "",
        label: str = "",
        labels: str = "",
        author: str = "",
        authors: str = "",
        source: str = "",
        order_by: str = "created",
        order_direction: str = "desc",
        cursor: int = Query(default=0, ge=0),
        limit: int = Query(default=50, ge=1, le=100),
    ) -> JSONResponse:
        try:
            ctx, repository, store = context(project_id)
            items = store.list()
            sync = store.sync_state()
            events = event_log_from_project(
                ctx.state_dir,
                config=ctx.config or workspace_config,
            ).read_all()
        except ValueError as exc:
            return JSONResponse({"ok": False, "status": "unconfigured", "reason": str(exc)}, status_code=409)
        needle = q.strip().casefold()[:200]
        requested_label = label.strip().casefold()
        requested_labels: set[str] | None = None
        if labels:
            try:
                raw_labels = json.loads(labels)
            except json.JSONDecodeError:
                raw_labels = []
            requested_labels = {
                str(value).strip().casefold()
                for value in raw_labels
                if str(value).strip()
            } if isinstance(raw_labels, list) else set()
        requested_authors: set[str] | None = None
        if authors:
            try:
                raw_authors = json.loads(authors)
            except json.JSONDecodeError:
                raw_authors = []
            requested_authors = {
                str(value).strip().casefold()
                for value in raw_authors
                if str(value).strip()
            } if isinstance(raw_authors, list) else set()
        filtered = [item for item in items if (
            (not group or item.derived_group == group)
            and (not state or item.github_state == state)
            and (not requested_label or requested_label in {value.casefold() for value in item.labels})
            and (requested_labels is None or bool(requested_labels & {value.casefold() for value in item.labels}))
            and (not author or item.author_login.casefold() == author.casefold())
            and (requested_authors is None or item.author_login.casefold() in requested_authors)
            and (not source or item.source == source)
            and (
                not needle
                or needle in item.title.casefold()
                or needle in item.author_login.casefold()
                or needle in str(item.number)
                or any(needle in value.casefold() for value in item.labels)
            )
        )]
        reverse = order_direction.strip().casefold() != "asc"
        if order_by.strip().casefold() == "name":
            filtered.sort(key=lambda item: (item.title.casefold(), item.number), reverse=reverse)
        else:
            filtered.sort(key=lambda item: (item.created_at, item.number), reverse=reverse)
        page = filtered[cursor:cursor + limit]
        next_cursor = cursor + len(page) if cursor + len(page) < len(filtered) else None
        return JSONResponse({
            "schema_version": "issue-triage-page.v1",
            "repository": repository,
            "items": [
                _public(item, workflow=_workflow_projection(events, item))
                for item in page
            ],
            "total": len(filtered),
            "cursor": cursor,
            "next_cursor": next_cursor,
            "sync": sync.to_dict(),
        })

    @router.get("/api/projects/{project_id}/issue-triage/{issue_number}")
    def issue_triage_detail(project_id: str, issue_number: int) -> JSONResponse:
        try:
            ctx, _, store = context(project_id)
            item = store.get(issue_number)
            if item is None:
                return JSONResponse({"ok": False, "status": "not_found"}, status_code=404)
            body = store.read_body(item)
            comments = store.read_comments(item)
            events = event_log_from_project(
                ctx.state_dir,
                config=ctx.config or workspace_config,
            ).read_all()
        except ValueError as exc:
            return JSONResponse({"ok": False, "status": "invalid_mirror", "reason": str(exc)}, status_code=409)
        return JSONResponse({
            "schema_version": "issue-triage-detail.v1",
            "issue": _public(
                item,
                workflow=_workflow_projection(events, item),
            ),
            "body": body,
            "comments": [comment.to_dict() for comment in comments],
            "trust": "untrusted_external_input",
        })

    @router.post("/api/projects/{project_id}/issue-triage/refresh")
    async def issue_triage_refresh(
        project_id: str,
        force: bool = Query(default=False),
    ) -> JSONResponse:
        try:
            ctx, repository, _ = context(project_id)
        except ValueError as exc:
            return JSONResponse({"ok": False, "status": "unconfigured", "reason": str(exc)}, status_code=409)
        effective_config = ctx.config or workspace_config
        if effective_config is not None and effective_config.self_issue.ingress.enabled:
            service = ExternalIssueIngressService(
                ctx.state_dir,
                effective_config,
                project_root=getattr(ctx, "project_root", ctx.state_dir.parent),
                reconciler=build_reconciler(ctx.state_dir, repository),
            )
            outcome = await asyncio.to_thread(service.poll_once, force=force)
            result = {
                "ok": outcome.ok,
                "status": outcome.status,
                "changed": outcome.changed,
                "received": outcome.received,
                "triage_queued": outcome.triage_queued,
                "error": outcome.error,
            }
        else:
            result = await asyncio.to_thread(
                build_reconciler(ctx.state_dir, repository).refresh,
                force=force,
            )
        return JSONResponse(result, status_code=200 if result.get("ok") else 502)

    @router.post("/api/projects/{project_id}/issue-triage/github-webhook")
    async def issue_triage_webhook(
        project_id: str,
        request: Request,
        x_github_event: str | None = Header(default=None),
        x_github_delivery: str | None = Header(default=None),
        x_hub_signature_256: str | None = Header(default=None),
    ) -> JSONResponse:
        secret = read_webhook_secret()
        if not secret:
            return JSONResponse({
                "ok": False,
                "status": "disabled",
                "reason": "ZF_GITHUB_TRIAGE_WEBHOOK_SECRET is not configured",
            }, status_code=503)
        raw = await request.body()
        if len(raw) > 2_000_000:
            return JSONResponse({"ok": False, "status": "payload_too_large"}, status_code=413)
        expected = "sha256=" + hmac.new(secret.encode("utf-8"), raw, hashlib.sha256).hexdigest()
        if not x_hub_signature_256 or not hmac.compare_digest(expected, x_hub_signature_256):
            return JSONResponse({"ok": False, "status": "invalid_signature"}, status_code=403)
        if not x_github_delivery:
            return JSONResponse({"ok": False, "status": "missing_delivery_id"}, status_code=422)
        try:
            _, repository, store = context(project_id)
            payload = json.loads(raw.decode("utf-8"))
            if not isinstance(payload, dict):
                raise ValueError("webhook payload must contain an object")
            repository_payload = payload.get("repository")
            if not isinstance(repository_payload, dict):
                raise ValueError("webhook repository is missing")
            repository_id = str(repository_payload.get("id") or "")
            full_name = str(repository_payload.get("full_name") or "")
            if full_name.casefold() != repository.casefold() or not repository_id:
                raise ValueError("webhook repository identity mismatch")
            current = store.sync_state()
            if current.repository_id and current.repository_id != repository_id:
                raise ValueError("webhook repository id mismatch")
            if x_github_event == "ping":
                if not store.claim_webhook_delivery(x_github_delivery):
                    return JSONResponse({"ok": True, "status": "duplicate"})
                return JSONResponse({"ok": True, "status": "pong"})
            if x_github_event != "issues":
                return JSONResponse({"ok": True, "status": "ignored"})
            issue = payload.get("issue")
            if not isinstance(issue, dict):
                raise ValueError("issues webhook is missing issue")
            seen_at = datetime_now_iso()
            mirror, body = normalize_github_issue(
                issue,
                repository=repository,
                repository_id=repository_id,
                seen_at=seen_at,
            )
            if not store.claim_webhook_delivery(x_github_delivery):
                return JSONResponse({"ok": True, "status": "duplicate"})
            _, changed = store.upsert(mirror, body)
            return JSONResponse({"ok": True, "status": "accepted", "changed": changed})
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
            return JSONResponse({
                "ok": False,
                "status": "invalid_payload",
                "reason": str(exc)[:300],
            }, status_code=422)

    return router


def datetime_now_iso() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


__all__ = ["build_issue_triage_router"]
