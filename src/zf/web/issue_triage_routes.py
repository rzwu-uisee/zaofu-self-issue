"""Read-only GitHub Issue Triage mirror routes and optional webhook ingress."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import os
from collections import Counter
from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, Header, Query, Request
from fastapi.responses import JSONResponse

from zf.core.config.schema import ZfConfig
from zf.core.issue_triage.models import IssueMirror
from zf.core.issue_triage.store import IssueMirrorStore
from zf.integrations.forge.github_issues import (
    GitHubIssueReconciler,
    normalize_github_issue,
)

ReconcilerFactory = Callable[[Any, str], GitHubIssueReconciler]


def _repository(config: ZfConfig | None) -> str:
    if config is None or not config.self_issue.enabled:
        return ""
    target = config.self_issue.targets.get("github")
    if target is not None:
        return target.project
    if config.self_issue.provider == "github":
        return config.self_issue.target_project
    return ""


def _public(item: IssueMirror) -> dict[str, Any]:
    return item.to_dict()


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
        authors = Counter(item.author_login for item in items)
        return JSONResponse({
            "schema_version": "issue-triage-summary.v1",
            "repository": repository,
            "repository_url": f"https://github.com/{repository}",
            "new_issue_url": f"https://github.com/{repository}/issues/new",
            "total": len(items),
            "groups": dict(groups),
            "states": dict(states),
            "labels": dict(sorted(labels.items())),
            "authors": dict(sorted(authors.items())),
            "sync": sync.to_dict(),
        })

    @router.get("/api/projects/{project_id}/issue-triage")
    def issue_triage_list(
        project_id: str,
        q: str = "",
        group: str = "",
        state: str = "",
        label: str = "",
        author: str = "",
        source: str = "",
        cursor: int = Query(default=0, ge=0),
        limit: int = Query(default=50, ge=1, le=100),
    ) -> JSONResponse:
        try:
            _, repository, store = context(project_id)
            items = store.list()
            sync = store.sync_state()
        except ValueError as exc:
            return JSONResponse({"ok": False, "status": "unconfigured", "reason": str(exc)}, status_code=409)
        needle = q.strip().casefold()[:200]
        requested_label = label.strip().casefold()
        filtered = [item for item in items if (
            (not group or item.derived_group == group)
            and (not state or item.github_state == state)
            and (not requested_label or requested_label in {value.casefold() for value in item.labels})
            and (not author or item.author_login.casefold() == author.casefold())
            and (not source or item.source == source)
            and (
                not needle
                or needle in item.title.casefold()
                or needle in item.author_login.casefold()
                or needle in str(item.number)
                or any(needle in value.casefold() for value in item.labels)
            )
        )]
        filtered.sort(key=lambda item: (item.updated_at, item.number), reverse=True)
        page = filtered[cursor:cursor + limit]
        next_cursor = cursor + len(page) if cursor + len(page) < len(filtered) else None
        return JSONResponse({
            "schema_version": "issue-triage-page.v1",
            "repository": repository,
            "items": [_public(item) for item in page],
            "total": len(filtered),
            "cursor": cursor,
            "next_cursor": next_cursor,
            "sync": sync.to_dict(),
        })

    @router.get("/api/projects/{project_id}/issue-triage/{issue_number}")
    def issue_triage_detail(project_id: str, issue_number: int) -> JSONResponse:
        try:
            _, _, store = context(project_id)
            item = store.get(issue_number)
            if item is None:
                return JSONResponse({"ok": False, "status": "not_found"}, status_code=404)
            body = store.read_body(item)
        except ValueError as exc:
            return JSONResponse({"ok": False, "status": "invalid_mirror", "reason": str(exc)}, status_code=409)
        return JSONResponse({
            "schema_version": "issue-triage-detail.v1",
            "issue": _public(item),
            "body": body,
            "trust": "untrusted_external_input",
        })

    @router.post("/api/projects/{project_id}/issue-triage/refresh")
    async def issue_triage_refresh(project_id: str) -> JSONResponse:
        try:
            ctx, repository, _ = context(project_id)
        except ValueError as exc:
            return JSONResponse({"ok": False, "status": "unconfigured", "reason": str(exc)}, status_code=409)
        result = await asyncio.to_thread(
            build_reconciler(ctx.state_dir, repository).refresh,
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
