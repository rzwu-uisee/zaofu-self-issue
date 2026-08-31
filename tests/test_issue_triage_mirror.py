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

from zf.core.config.schema import (
    ExternalIssueIngressConfig,
    SelfIssueConfig,
    SelfIssueTargetConfig,
    ZfConfig,
)
from zf.core.events import EventLog, EventWriter
from zf.core.issue_triage.store import IssueMirrorStore
from zf.integrations.forge.github_issues import (
    GitHubIssueReconciler,
    normalize_github_comment,
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
        "user": {
            "login": "reporter",
            "avatar_url": "https://avatars.githubusercontent.com/u/123?v=4",
        },
        "state": "open",
        "created_at": "2026-08-25T00:00:00Z",
        "updated_at": updated_at,
        "closed_at": None,
        "labels": [{"name": item} for item in labels],
        "assignees": [{
            "login": "maintainer",
            "avatar_url": "https://avatars.githubusercontent.com/u/321?v=4",
        }],
        "comments": 2,
        "milestone": {"title": "P0"},
    }


def comment_payload(comment_id: int = 91) -> dict:
    return {
        "id": comment_id,
        "node_id": f"IC_{comment_id}",
        "html_url": f"https://github.com/rzwu-uisee/zaofu-self-issue/issues/7#issuecomment-{comment_id}",
        "body": "Screenshot: ![capture](https://github.com/user-attachments/assets/example.png)",
        "user": {
            "login": "commenter",
            "avatar_url": "https://avatars.githubusercontent.com/u/789?v=4",
        },
        "created_at": "2026-08-25T02:10:00Z",
        "updated_at": "2026-08-25T02:10:00Z",
        "author_association": "CONTRIBUTOR",
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
    assert item.author_avatar_url == "https://avatars.githubusercontent.com/u/123?v=4"
    assert item.assignee_avatar_urls == {
        "maintainer": "https://avatars.githubusercontent.com/u/321?v=4",
    }
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

    comment = normalize_github_comment(comment_payload())
    assert comment.author_login == "commenter"
    assert "user-attachments" in comment.body


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
    comment = normalize_github_comment(comment_payload())
    with_comments, changed = store.write_comments(retained, [comment])
    assert changed
    assert store.read_comments(with_comments) == [comment]


def test_reconciler_verifies_repository_filters_pr_and_debounces(tmp_path: Path) -> None:
    calls: list[str] = []

    def transport(method: str, url: str, headers: dict[str, str]):
        del method, headers
        calls.append(url)
        if url.endswith("/repos/rzwu-uisee/zaofu-self-issue"):
            return 200, json.dumps({
                "id": 123,
                "full_name": "rzwu-uisee/zaofu-self-issue",
                "stargazers_count": 64500,
            }).encode(), {}
        if "/comments?" in url:
            return 200, json.dumps([comment_payload()]).encode(), {}
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
    assert reconciler.refresh() == {"ok": True, "status": "fresh", "changed": 2}
    assert [item.number for item in reconciler.store.list()] == [7]
    assert reconciler.store.sync_state().rate_limit_remaining == 59
    assert reconciler.store.sync_state().star_count == 64500
    mirrored = reconciler.store.get(7)
    assert mirrored is not None
    assert reconciler.store.read_comments(mirrored)[0].author_login == "commenter"
    assert reconciler.refresh().get("debounced") is True
    assert len(calls) == 3


def test_reconciler_can_fetch_one_unmirrored_issue(tmp_path: Path) -> None:
    calls: list[str] = []

    def transport(method: str, url: str, headers: dict[str, str]):
        del method, headers
        calls.append(url)
        if url.endswith("/repos/rzwu-uisee/zaofu-self-issue"):
            return 200, json.dumps({
                "id": 123,
                "full_name": "rzwu-uisee/zaofu-self-issue",
                "stargazers_count": 7,
            }).encode(), {}
        if "/comments?" in url:
            return 200, json.dumps([comment_payload()]).encode(), {}
        if url.endswith("/issues/7"):
            return 200, json.dumps(issue_payload()).encode(), {
                "X-RateLimit-Remaining": "58",
            }
        raise AssertionError(url)

    reconciler = GitHubIssueReconciler(
        tmp_path / "state",
        "rzwu-uisee/zaofu-self-issue",
        transport=transport,
        now=lambda: datetime(2026, 8, 25, 2, tzinfo=timezone.utc),
    )

    result = reconciler.refresh_issue(7)

    assert result == {"ok": True, "status": "fresh", "changed": 2, "issue_number": 7}
    assert reconciler.store.get(7) is not None
    assert reconciler.store.sync_state().rate_limit_remaining == 58
    assert not any("/issues?" in url for url in calls)


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
    webhook_issue = issue_payload()
    webhook_issue["labels"][0]["color"] = "D93F0B"
    raw = json.dumps({
        "action": "opened",
        "repository": {"id": 123, "full_name": "rzwu-uisee/zaofu-self-issue"},
        "issue": webhook_issue,
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
    assert page["items"][0]["author_avatar_url"].startswith("https://avatars.githubusercontent.com/")
    assert client.get("/api/projects/test/issue-triage/summary").json()["label_colors"] == {
        "performance": "D93F0B",
    }
    assert client.get("/api/projects/test/issue-triage/summary").json()["author_states"] == {
        "reporter": {"open": 1, "closed": 0},
    }
    assert client.get(
        "/api/projects/test/issue-triage",
        params={"labels": json.dumps(["performance"])},
    ).json()["total"] == 1
    assert client.get(
        "/api/projects/test/issue-triage",
        params={"labels": json.dumps([])},
    ).json()["total"] == 0

    second_payload = issue_payload(8, labels=("p1",))
    second_payload["title"] = "Alpha issue"
    second_payload["created_at"] = "2026-08-24T00:00:00Z"
    second_payload["updated_at"] = "2026-08-26T00:00:00Z"
    second_payload["user"] = {
        "login": "another-reporter",
        "avatar_url": "https://avatars.githubusercontent.com/u/456?v=4",
    }
    second_payload["labels"] = [{"name": "p1", "color": "B60205"}]
    second, second_body = normalize_github_issue(
        second_payload,
        repository="rzwu-uisee/zaofu-self-issue",
        repository_id="123",
        seen_at="2026-08-26T00:01:00+00:00",
    )
    IssueMirrorStore(ctx.state_dir).upsert(second, second_body)
    stored_first = IssueMirrorStore(ctx.state_dir).get(7)
    assert stored_first is not None
    IssueMirrorStore(ctx.state_dir).write_comments(
        stored_first,
        [normalize_github_comment(comment_payload())],
    )
    assert client.get(
        "/api/projects/test/issue-triage",
        params={"authors": json.dumps(["another-reporter"])},
    ).json()["items"][0]["number"] == 8
    assert client.get(
        "/api/projects/test/issue-triage",
        params={"authors": json.dumps([])},
    ).json()["total"] == 0
    by_name = client.get(
        "/api/projects/test/issue-triage",
        params={"order_by": "name", "order_direction": "asc"},
    ).json()
    assert [item["number"] for item in by_name["items"]] == [8, 7]
    by_created = client.get(
        "/api/projects/test/issue-triage",
        params={"order_by": "created", "order_direction": "asc"},
    ).json()
    assert [item["number"] for item in by_created["items"]] == [8, 7]
    detail = client.get("/api/projects/test/issue-triage/7").json()
    assert detail["body"] == "Observed slowdown"
    assert detail["comments"][0]["author_login"] == "commenter"
    assert "user-attachments" in detail["comments"][0]["body"]
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
    assert "/api/projects/{project_id}/issue-triage/start-triage" in paths
    assert "/api/projects/{project_id}/issue-triage/attachment" in paths
    assert "/api/projects/{project_id}/issue-triage/github-webhook" in paths


def test_attachment_proxy_only_serves_github_images(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ctx = configured_context(tmp_path / "state")

    class ImageResponse:
        headers = {"Content-Type": "image/png"}

        def __enter__(self) -> ImageResponse:
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def read(self, limit: int) -> bytes:
            assert limit == 20_000_001
            return b"\x89PNG\r\n"

    def fake_urlopen(request: object, timeout: int) -> ImageResponse:
        assert getattr(request, "full_url", "").startswith(
            "https://github.com/user-attachments/assets/",
        )
        assert timeout == 20
        return ImageResponse()

    monkeypatch.setattr("zf.web.issue_triage_routes.urllib.request.urlopen", fake_urlopen)
    app = FastAPI()
    app.include_router(build_issue_triage_router(resolve_ctx=lambda project_id: ctx))
    client = TestClient(app)
    response = client.get(
        "/api/projects/test/issue-triage/attachment",
        params={"url": "https://github.com/user-attachments/assets/example.png"},
    )
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/png"
    assert response.content == b"\x89PNG\r\n"
    assert client.get(
        "/api/projects/test/issue-triage/attachment",
        params={"url": "https://example.com/private.png"},
    ).status_code == 400


def test_manual_refresh_forces_repository_metadata_sync(tmp_path: Path) -> None:
    ctx = configured_context(tmp_path / "state")
    force_values: list[bool] = []

    class Reconciler:
        def refresh(self, *, force: bool = False) -> dict:
            force_values.append(force)
            return {"ok": True, "status": "fresh", "changed": 0}

    app = FastAPI()
    app.include_router(build_issue_triage_router(
        resolve_ctx=lambda project_id: ctx,
        reconciler_factory=lambda state_dir, repository: Reconciler(),  # type: ignore[arg-type]
    ))
    client = TestClient(app)
    assert client.post(
        "/api/projects/test/issue-triage/refresh?force=true",
    ).status_code == 200
    assert force_values == [True]


def test_manual_start_triage_admits_one_historical_issue(tmp_path: Path) -> None:
    ctx = configured_context(tmp_path / "state")
    ctx.state_dir.mkdir(parents=True)
    (ctx.state_dir / "kanban.json").write_text("[]\n", encoding="utf-8")
    ctx.config.self_issue.ingress = ExternalIssueIngressConfig(enabled=True)
    mirror, body = normalize_github_issue(
        issue_payload(),
        repository="rzwu-uisee/zaofu-self-issue",
        repository_id="123",
        seen_at="2026-08-25T02:00:00+00:00",
    )
    IssueMirrorStore(ctx.state_dir).upsert(mirror, body)
    requested: list[int] = []

    class Reconciler:
        def refresh_issue(self, issue_number: int) -> dict:
            requested.append(issue_number)
            return {"ok": True, "status": "fresh", "changed": 0, "issue_number": issue_number}

    app = FastAPI()
    app.include_router(build_issue_triage_router(
        resolve_ctx=lambda project_id: ctx,
        reconciler_factory=lambda state_dir, repository: Reconciler(),  # type: ignore[arg-type]
        mutation_auth_error=lambda *args, **kwargs: None,
    ))
    client = TestClient(app)

    wrong_repo = client.post(
        "/api/projects/test/issue-triage/start-triage",
        json={"issue": "https://github.com/another/project/issues/7"},
    )
    response = client.post(
        "/api/projects/test/issue-triage/start-triage",
        json={"issue": "#7"},
    )
    duplicate = client.post(
        "/api/projects/test/issue-triage/start-triage",
        json={"issue": mirror.html_url},
    )

    assert wrong_repo.status_code == 422
    assert response.status_code == 200
    assert response.json()["status"] == "queued"
    assert response.json()["issue"]["workflow"]["state"] == "triage_queued"
    assert duplicate.json()["status"] == "already_queued"
    assert requested == [7, 7]


def test_manual_start_triage_requires_mutation_authorization(tmp_path: Path) -> None:
    ctx = configured_context(tmp_path / "state")
    app = FastAPI()
    app.include_router(build_issue_triage_router(resolve_ctx=lambda project_id: ctx))

    response = TestClient(app).post(
        "/api/projects/test/issue-triage/start-triage",
        json={"issue": "#7"},
    )

    assert response.status_code == 503
    assert response.json()["status"] == "disabled"


def test_list_and_detail_project_external_issue_workflow_state(tmp_path: Path) -> None:
    ctx = configured_context(tmp_path / "state")
    mirror, body = normalize_github_issue(
        issue_payload(),
        repository="rzwu-uisee/zaofu-self-issue",
        repository_id="123",
        seen_at="2026-08-25T02:00:00+00:00",
    )
    IssueMirrorStore(ctx.state_dir).upsert(mirror, body)
    writer = EventWriter(EventLog(ctx.state_dir / "events.jsonl"))
    received = writer.emit(
        "external_issue.received",
        actor="github-poller",
        payload={
            "source_key": mirror.issue_key,
            "source_revision": "sha256:revision",
        },
    )
    writer.emit(
        "external_issue.triage.queued",
        actor="external-issue-intake",
        task_id="ISSUE-123",
        causation_id=received.id,
        payload={
            "source_key": mirror.issue_key,
            "source_revision": "sha256:revision",
            "workflow_run_id": "TRIAGE-123",
            "invoke_event_id": "evt-invoke",
            "status": "triage_queued",
        },
    )
    app = FastAPI()
    app.include_router(build_issue_triage_router(resolve_ctx=lambda project_id: ctx))
    client = TestClient(app)

    item = client.get("/api/projects/test/issue-triage").json()["items"][0]
    detail = client.get("/api/projects/test/issue-triage/7").json()["issue"]

    assert item["workflow"] == detail["workflow"]
    assert item["workflow"]["state"] == "triage_queued"
    assert item["workflow"]["task_id"] == "ISSUE-123"
    assert item["workflow"]["source_revision"] == "sha256:revision"
