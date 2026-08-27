from __future__ import annotations

import json
from urllib.parse import parse_qs

from zf.integrations.forge.base import (
    AttachmentUploadRequest,
    ForgeResult,
    IssuePublishRequest,
    PublishedIssue,
    UploadedAttachment,
)
from zf.integrations.forge.gitlab import GitLabComProvider
from zf.integrations.forge.github import GitHubComProvider, GitHubDeviceOAuthClient
from zf.integrations.forge.oauth import GitLabOAuthClient, pkce_pair


def test_gitlab_provider_uses_marker_and_does_not_put_token_in_body() -> None:
    calls = []

    def transport(method, url, headers, body):
        calls.append((method, url, headers, body))
        return 201, json.dumps({"iid": 7, "web_url": "https://gitlab.com/a/b/-/issues/7"}).encode()

    provider = GitLabComProvider(transport)
    result = provider.publish(IssuePublishRequest(
        project="a/b", title="bug",
        body="safe\n\n<!-- zf-self-issue:stable -->",
        labels=("runtime",), marker="stable",
    ), access_token="top-secret")

    assert result.status == "published"
    assert b"top-secret" not in calls[0][3]
    posted = parse_qs(calls[0][3].decode())
    assert posted["description"] == ["safe\n\n<!-- zf-self-issue:stable -->"]
    assert calls[0][2]["Authorization"] == "Bearer top-secret"


def test_oauth_uses_s256_and_exact_redirect_uri() -> None:
    verifier, challenge = pkce_pair()
    assert verifier and challenge and "=" not in challenge
    client = GitLabOAuthClient(lambda *_: (500, b"{}"))
    url = client.authorization_url(
        client_id="client", redirect_uri="https://example.test/exact/callback",
        state="once", challenge=challenge,
    )
    query = parse_qs(url.split("?", 1)[1])
    assert query["code_challenge_method"] == ["S256"]
    assert query["redirect_uri"] == ["https://example.test/exact/callback"]
    assert query["scope"] == ["api"]

    exchange = GitLabOAuthClient(lambda *_: (200, json.dumps({
        "access_token": "secret", "refresh_token": "refresh", "scope": ["api"],
    }).encode()))
    token = exchange.exchange(
        code="code", verifier=verifier, client_id="client",
        redirect_uri="https://example.test/exact/callback",
    )
    assert token["scope"] == "api"


def test_oauth_refresh_rotates_public_client_token_without_client_secret() -> None:
    calls = []

    def transport(method, url, headers, body):
        calls.append((method, url, headers, body))
        return 200, json.dumps({
            "access_token": "next-access", "refresh_token": "next-refresh",
            "scope": ["api"], "expires_in": 7200, "created_at": 1_700_000_000,
        }).encode()

    token = GitLabOAuthClient(transport).refresh(
        refresh_token="current-refresh", client_id="client",
        redirect_uri="https://example.test/exact/callback",
    )

    posted = parse_qs(calls[0][3].decode())
    assert posted == {
        "client_id": ["client"],
        "refresh_token": ["current-refresh"],
        "grant_type": ["refresh_token"],
        "redirect_uri": ["https://example.test/exact/callback"],
    }
    assert "client_secret" not in posted
    assert token["access_token"] == "next-access"
    assert token["refresh_token"] == "next-refresh"
    assert token["scope"] == "api"
    assert token["expires_at"] == "2023-11-15T00:13:20+00:00"


def test_gitlab_ambiguous_server_response_is_outcome_unknown() -> None:
    provider = GitLabComProvider(lambda *_: (502, b"bad gateway"))
    result = provider.publish(IssuePublishRequest(
        project="a/b", title="bug", body="safe", labels=(), marker="stable",
    ), access_token="top-secret")
    assert result.status == "outcome_unknown"


def test_gitlab_rejected_credential_is_recoverable_authorization() -> None:
    provider = GitLabComProvider(lambda *_: (401, b'{"message":"401 Unauthorized"}'))
    result = provider.publish(IssuePublishRequest(
        project="a/b", title="bug", body="safe", labels=(), marker="stable",
    ), access_token="expired-secret")

    assert result.status == "authorization_required"
    assert result.reason == "credential_rejected"


def test_gitlab_marker_recovery_requires_exact_marker_and_safe_issue_url() -> None:
    payload = json.dumps([
        {"iid": 1, "web_url": "https://gitlab.com/a/b/-/issues/1",
         "description": "similar zf-self-issue:stable"},
        {"iid": 2, "web_url": "https://evil.test/token",
         "description": "<!-- zf-self-issue:stable -->"},
        {"iid": 3, "web_url": "https://gitlab.com/a/b/-/issues/3",
         "description": "<!-- zf-self-issue:stable -->"},
    ]).encode()
    provider = GitLabComProvider(lambda *_: (200, payload))

    matches = provider.find_by_marker("a/b", "stable", access_token="top-secret")
    assert [item.number for item in matches] == ["3"]


def test_gitlab_attachment_upload_uses_multipart_without_leaking_token() -> None:
    calls = []

    def transport(method, url, headers, body):
        calls.append((method, url, headers, body))
        return 201, json.dumps({
            "id": 9,
            "markdown": "[safe.log](/uploads/abc/safe.log)",
            "full_path": "/a/b/-/uploads/abc/safe.log",
        }).encode()

    result = GitLabComProvider(transport).upload_attachment(
        AttachmentUploadRequest(
            project="a/b", filename="safe.log", content_type="text/plain",
            content=b"safe evidence", digest="abc",
        ),
        access_token="top-secret",
    )

    assert result.status == "published"
    assert result.attachment is not None
    assert result.attachment.url == "https://gitlab.com/a/b/-/uploads/abc/safe.log"
    assert b"top-secret" not in calls[0][3]
    assert b"safe evidence" in calls[0][3]
    assert calls[0][2]["Authorization"] == "Bearer top-secret"


def test_fake_github_implements_the_provider_neutral_issue_and_attachment_contract() -> None:
    class FakeGithub:
        name = "github"

        def publish(self, request, *, access_token):
            return ForgeResult(status="published", issue=PublishedIssue(
                provider=self.name, project=request.project, number="1",
                url="https://example.test/issues/1",
            ))

        def upload_attachment(self, request, *, access_token):
            return ForgeResult(status="published", attachment=UploadedAttachment(
                provider=self.name, project=request.project, filename=request.filename,
                markdown="[file](https://example.test/file)",
                url="https://example.test/file",
            ))

        def find_by_marker(self, project, marker, *, access_token):
            return []

    provider = FakeGithub()
    issue = provider.publish(IssuePublishRequest(
        project="owner/repo", title="bug", body="body", labels=("runtime",), marker="m",
    ), access_token="fake")
    attachment = provider.upload_attachment(AttachmentUploadRequest(
        project="owner/repo", filename="safe.log", content_type="text/plain",
        content=b"safe", digest="abc",
    ), access_token="fake")
    assert issue.issue.provider == attachment.attachment.provider == "github"


def test_github_provider_uses_rest_issue_contract_and_exact_marker() -> None:
    calls = []

    def transport(method, url, headers, body):
        calls.append((method, url, headers, body))
        return 201, json.dumps({
            "number": 9,
            "html_url": "https://github.com/owner/repo/issues/9",
        }).encode()

    provider = GitHubComProvider(transport)
    result = provider.publish(IssuePublishRequest(
        project="owner/repo",
        title="bug",
        body="safe\n\n<!-- zf-self-issue:stable -->",
        labels=("runtime",),
        marker="stable",
    ), access_token="top-secret")

    assert result.status == "published"
    assert calls[0][0] == "POST"
    assert calls[0][1].endswith("/repos/owner/repo/issues")
    assert calls[0][2]["Authorization"] == "Bearer top-secret"
    assert b"top-secret" not in calls[0][3]
    assert json.loads(calls[0][3]) == {
        "title": "bug",
        "body": "safe\n\n<!-- zf-self-issue:stable -->",
    }
    assert provider.capabilities.binary_attachment_upload is False


def test_github_provider_creates_and_applies_requested_labels() -> None:
    calls = []

    def transport(method, url, headers, body):
        calls.append((method, url, headers, body))
        if url.endswith("/issues"):
            return 201, json.dumps({
                "number": 9,
                "html_url": "https://github.com/owner/repo/issues/9",
            }).encode()
        if url.endswith("/labels"):
            return 201, b"{}"
        if url.endswith("/issues/9/labels"):
            return 200, b"[]"
        raise AssertionError(url)

    result = GitHubComProvider(transport).publish(IssuePublishRequest(
        project="owner/repo", title="bug", body="safe",
        labels=("runtime", "p2"), marker="stable",
    ), access_token="top-secret")

    assert result.status == "published"
    assert [call[0] for call in calls] == ["POST", "POST", "POST", "POST"]
    assert json.loads(calls[1][3]) == {"name": "runtime", "color": "5319E7"}
    assert json.loads(calls[2][3]) == {"name": "p2", "color": "FBCA04"}
    assert json.loads(calls[3][3]) == {"labels": ["runtime", "p2"]}
    assert all(b"top-secret" not in call[3] for call in calls)


def test_github_marker_recovery_rejects_pull_requests_and_unsafe_urls() -> None:
    payload = json.dumps([
        {
            "number": 1,
            "html_url": "https://github.com/owner/repo/issues/1",
            "body": "<!-- zf-self-issue:stable -->",
            "pull_request": {},
        },
        {
            "number": 2,
            "html_url": "https://evil.test/issues/2",
            "body": "<!-- zf-self-issue:stable -->",
        },
        {
            "number": 3,
            "html_url": "https://github.com/owner/repo/issues/3",
            "body": "<!-- zf-self-issue:stable -->",
        },
    ]).encode()
    provider = GitHubComProvider(lambda *_: (200, payload))

    matches = provider.find_by_marker("owner/repo", "stable", access_token="secret")

    assert [item.number for item in matches] == ["3"]


def test_github_device_flow_uses_client_id_without_client_secret() -> None:
    calls = []
    responses = iter([
        (200, json.dumps({
            "device_code": "device-secret",
            "user_code": "ABCD-EFGH",
            "verification_uri": "https://github.com/login/device",
            "expires_in": 900,
            "interval": 5,
        }).encode()),
        (200, json.dumps({
            "access_token": "access-secret",
            "refresh_token": "refresh-secret",
            "expires_in": 28800,
            "refresh_token_expires_in": 15897600,
            "token_type": "bearer",
        }).encode()),
    ])

    def transport(method, url, headers, body):
        calls.append((method, url, headers, body))
        return next(responses)

    client = GitHubDeviceOAuthClient(transport)
    started = client.start(client_id="Iv-client")
    token = client.poll(client_id="Iv-client", device_code=started["device_code"])

    assert started["user_code"] == "ABCD-EFGH"
    assert token["status"] == "connected"
    assert token["scope"] == "issues:write"
    assert all(b"client_secret" not in call[3] for call in calls)
