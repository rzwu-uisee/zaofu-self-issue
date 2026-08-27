from __future__ import annotations

import hashlib
import hmac
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from zf.core.config.schema import SelfIssueConfig, SelfIssueTargetConfig, ZfConfig
from zf.core.issue_triage.store import IssueMirrorStore
from zf.integrations.forge.github_issues import (
    GitHubIssueReconciler,
    normalize_github_issue,
)
from zf.web.issue_triage_routes import build_issue_triage_router
from zf.web.server import create_app


def issue_payload(
    number: int = 7,
    *,
    updated_at: str = "2026-08-25T01:00:00Z",
    labels: tuple[str, ...] = ("performance", "p2"),
    body: str = "Observed slowdown",
) -> dict:
    return {
        "number": number,
        "node_id": f"I_{number}",
        "html_url": f"https://github.com/rzwu-uisee/zaofu-self-issue/issues/{number}",
        "title": f"Issue {number}",
        "body": body,
        "user": {"login": "reporter"},
        "state": "open",
        "created_at": "2026-08-25T00:00:00Z",
        "updated_at": updated_at,
        "closed_at": None,
        "labels": [{"name": item} for item in labels],
        "assignees": [{"login": "maintainer"}],
        "comments": 2,
        "milestone": {"title": "P0"},
    }


def test_normalization_derives_group_and_rejects_pull_requests() -> None:
    item, body = normalize_github_issue(
        issue_payload(body="<!-- zf-self-issue:stable -->\nObserved slowdown"),
        repository="rzwu-uisee/zaofu-self-issue",
        repository_id="123",
        seen_at="2026-08-25T02:00:00+00:00",
    )
    assert item.derived_group == "triaged"
    assert item.source == "self_issue"
    assert body.startswith("<!-- zf-self-issue:")

    colored, _ = normalize_github_issue(
        issue_payload(labels=("performance",)),
        repository="rzwu-uisee/zaofu-self-issue",
        repository_id="123",
        seen_at="2026-08-25T02:00:00+00:00",
    )
    assert colored.label_colors == {}

    payload = issue_payload(labels=("performance",))
    payload["labels"] = [{"name": "performance", "color": "D93F0B"}]
    colored, _ = normalize_github_issue(
        payload,
        repository="rzwu-uisee/zaofu-self-issue",
        repository_id="123",
        seen_at="2026-08-25T02:00:00+00:00",
    )
    assert colored.label_colors == {"performance": "D93F0B"}

    pull_request = issue_payload()
    pull_request["pull_request"] = {"url": "https://api.github.com/pulls/7"}
    with pytest.raises(ValueError, match="pull_request_not_issue"):
        normalize_github_issue(
            pull_request,
            repository="rzwu-uisee/zaofu-self-issue",
            repository_id="123",
            seen_at="2026-08-25T02:00:00+00:00",
        )


def test_store_keeps_body_sidecar_and_rejects_older_revision(tmp_path: Path) -> None:
    store = IssueMirrorStore(tmp_path / "state")
    newer, body = normalize_github_issue(
        issue_payload(updated_at="2026-08-25T02:00:00Z"),
        repository="rzwu-uisee/zaofu-self-issue",
        repository_id="123",
        seen_at="2026-08-25T02:01:00+00:00",
    )
    saved, changed = store.upsert(newer, body)
    assert changed
    assert "body" not in json.loads(store.issues_path.read_text(encoding="utf-8"))[0]
    assert store.read_body(saved) == body

    older, older_body = normalize_github_issue(
        issue_payload(updated_at="2026-08-25T01:00:00Z", body="stale"),
        repository="rzwu-uisee/zaofu-self-issue",
        repository_id="123",
        seen_at="2026-08-25T02:02:00+00:00",
    )
    retained, changed = store.upsert(older, older_body)
    assert not changed
    assert retained.updated_at == "2026-08-25T02:00:00Z"
    assert store.read_body(retained) == body


def test_reconciler_verifies_repository_filters_pr_and_debounces(tmp_path: Path) -> None:
    calls: list[str] = []

    def transport(method: str, url: str, headers: dict[str, str]):
        del method, headers
        calls.append(url)
        if url.endswith("/repos/rzwu-uisee/zaofu-self-issue"):
            return 200, json.dumps({
                "id": 123,
                "full_name": "rzwu-uisee/zaofu-self-issue",
            }).encode(), {}
        pull_request = issue_payload(8)
        pull_request["pull_request"] = {"url": "https://api.github.com/pulls/8"}
        return 200, json.dumps([issue_payload(), pull_request]).encode(), {
            "ETag": '"one"',
            "X-RateLimit-Remaining": "59",
        }

    reconciler = GitHubIssueReconciler(
        tmp_path / "state",
        "rzwu-uisee/zaofu-self-issue",
        transport=transport,
        now=lambda: datetime(2026, 8, 25, 2, tzinfo=timezone.utc),
    )
    assert reconciler.refresh() == {"ok": True, "status": "fresh", "changed": 1}
    assert [item.number for item in reconciler.store.list()] == [7]
    assert reconciler.store.sync_state().rate_limit_remaining == 59
    assert reconciler.refresh().get("debounced") is True
    assert len(calls) == 2


@dataclass
class FakeContext:
    state_dir: Path
    config: ZfConfig


def configured_context(state_dir: Path) -> FakeContext:
    config = ZfConfig()
    config.self_issue = SelfIssueConfig(
        enabled=True,
        target_locked=True,
        targets={
            "github": SelfIssueTargetConfig(
                provider="github",
                authorization_domain="github.com",
                project="rzwu-uisee/zaofu-self-issue",
            ),
        },
    )
    return FakeContext(state_dir=state_dir, config=config)


def test_routes_list_detail_and_signed_webhook(tmp_path: Path) -> None:
    ctx = configured_context(tmp_path / "state")
    app = FastAPI()
    app.include_router(build_issue_triage_router(
        resolve_ctx=lambda project_id: ctx,
        webhook_secret=lambda: "test-secret",
    ))
    client = TestClient(app)
    raw = json.dumps({
        "action": "opened",
        "repository": {"id": 123, "full_name": "rzwu-uisee/zaofu-self-issue"},
        "issue": issue_payload(),
    }).encode()
    signature = "sha256=" + hmac.new(b"test-secret", raw, hashlib.sha256).hexdigest()
    headers = {
        "X-GitHub-Event": "issues",
        "X-GitHub-Delivery": "delivery-1",
        "X-Hub-Signature-256": signature,
        "Content-Type": "application/json",
    }
    response = client.post(
        "/api/projects/test/issue-triage/github-webhook",
        content=raw,
        headers=headers,
    )
    assert response.status_code == 200
    assert response.json()["status"] == "accepted"
    assert client.post(
        "/api/projects/test/issue-triage/github-webhook",
        content=raw,
        headers=headers,
    ).json()["status"] == "duplicate"

    page = client.get("/api/projects/test/issue-triage?group=triaged").json()
    assert page["total"] == 1
    assert page["items"][0]["number"] == 7
    detail = client.get("/api/projects/test/issue-triage/7").json()
    assert detail["body"] == "Observed slowdown"
    assert detail["trust"] == "untrusted_external_input"

    invalid = client.post(
        "/api/projects/test/issue-triage/github-webhook",
        content=raw,
        headers={**headers, "X-Hub-Signature-256": "sha256=bad"},
    )
    assert invalid.status_code == 403


def test_create_app_registers_issue_triage_routes(tmp_path: Path) -> None:
    ctx = configured_context(tmp_path / "state")
    app = create_app(ctx.state_dir, config=ctx.config, project_root=tmp_path)
    paths = {getattr(route, "path", "") for route in app.routes}
    assert "/api/projects/{project_id}/issue-triage" in paths
    assert "/api/projects/{project_id}/issue-triage/refresh" in paths
    assert "/api/projects/{project_id}/issue-triage/github-webhook" in paths
