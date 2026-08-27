"""GitLab.com OAuth Authorization Code + PKCE client."""

from __future__ import annotations

import base64
import hashlib
import json
import secrets
import urllib.parse
from datetime import datetime, timedelta, timezone

from zf.integrations.forge.gitlab import Transport, _stdlib_transport


def pkce_pair() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")
    return verifier, challenge


class GitLabOAuthClient:
    base_url = "https://gitlab.com"

    def __init__(self, transport: Transport | None = None) -> None:
        self.transport = transport or _stdlib_transport

    def authorization_url(
        self, *, client_id: str, redirect_uri: str, state: str, challenge: str,
    ) -> str:
        return f"{self.base_url}/oauth/authorize?" + urllib.parse.urlencode({
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "state": state,
            "scope": "api",
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        })

    def exchange(
        self, *, code: str, verifier: str, client_id: str, redirect_uri: str,
    ) -> dict[str, str]:
        payload = urllib.parse.urlencode({
            "client_id": client_id,
            "code": code,
            "grant_type": "authorization_code",
            "redirect_uri": redirect_uri,
            "code_verifier": verifier,
        }).encode("utf-8")
        status, raw = self.transport(
            "POST", f"{self.base_url}/oauth/token",
            {"Content-Type": "application/x-www-form-urlencoded"}, payload,
        )
        if status != 200:
            raise ValueError(f"oauth_exchange_failed:{status}")
        return self._token_response(raw)

    def refresh(
        self, *, refresh_token: str, client_id: str, redirect_uri: str,
    ) -> dict[str, str]:
        payload = urllib.parse.urlencode({
            "client_id": client_id,
            "refresh_token": refresh_token,
            "grant_type": "refresh_token",
            "redirect_uri": redirect_uri,
        }).encode("utf-8")
        status, raw = self.transport(
            "POST", f"{self.base_url}/oauth/token",
            {"Content-Type": "application/x-www-form-urlencoded"}, payload,
        )
        if status != 200:
            raise ValueError(f"oauth_refresh_failed:{status}")
        return self._token_response(raw)

    @staticmethod
    def _token_response(raw: bytes) -> dict[str, str]:
        value = json.loads(raw.decode("utf-8"))
        token = str(value.get("access_token") or "")
        if not token:
            raise ValueError("oauth_exchange_missing_access_token")
        raw_scope = value.get("scope") or ""
        scope = " ".join(str(item) for item in raw_scope) if isinstance(raw_scope, list) else str(raw_scope)
        result = {
            "access_token": token,
            "refresh_token": str(value.get("refresh_token") or ""),
            "scope": scope,
            "token_type": str(value.get("token_type") or "Bearer"),
        }
        expires_in = int(value.get("expires_in") or 0)
        if expires_in > 0:
            created_at = int(value.get("created_at") or datetime.now(timezone.utc).timestamp())
            result["expires_at"] = (
                datetime.fromtimestamp(created_at, tz=timezone.utc).replace(microsecond=0)
                + timedelta(seconds=expires_in)
            ).isoformat()
        return result
