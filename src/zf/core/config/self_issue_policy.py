"""Helpers for propagating a locked Self-Issue policy across a Workspace."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from typing import Any

from zf.core.config.schema import SelfIssueConfig, ZfConfig


def locked_self_issue_policy(config: ZfConfig | None) -> SelfIssueConfig | None:
    if config is None:
        return None
    policy = config.self_issue
    if not (
        policy.enabled
        and policy.target_locked
        and (policy.target_project or policy.targets)
    ):
        return None
    return policy


def inherit_workspace_self_issue_config(
    project_config: ZfConfig | None,
    workspace_config: ZfConfig | None,
) -> ZfConfig | None:
    """Apply the Web server's locked policy to one registered Project context."""

    policy = locked_self_issue_policy(workspace_config)
    if project_config is None or policy is None:
        return project_config
    return replace(project_config, self_issue=replace(policy))


def inject_self_issue_policy(
    documents: list[dict[str, Any]],
    policy: SelfIssueConfig | None,
) -> list[dict[str, Any]]:
    """Materialize a locked policy into a newly generated ZfConfig document."""

    if not (
        policy is not None
        and policy.enabled
        and policy.target_locked
        and (policy.target_project or policy.targets)
    ):
        return documents
    updated = deepcopy(documents)
    value: dict[str, Any] = {
        "enabled": True,
        "provider": policy.provider,
        "authorization_domain": policy.authorization_domain,
        "target_project": policy.target_project,
        "target_locked": True,
        "automatic_detection_enabled": policy.automatic_detection_enabled,
        "browser_capture_enabled": policy.browser_capture_enabled,
        "ingress": {
            "enabled": policy.ingress.enabled,
            "provider": policy.ingress.provider,
            "mode": policy.ingress.mode,
            "poll_interval_seconds": policy.ingress.poll_interval_seconds,
            "approval_label": policy.ingress.approval_label,
            "target_root": policy.ingress.target_root,
            "auto_triage_new_only": policy.ingress.auto_triage_new_only,
        },
        "delivery": {
            "enabled": policy.delivery.enabled,
            "provider": policy.delivery.provider,
            "repository": policy.delivery.repository,
            "remote_url": policy.delivery.remote_url,
            "base_branch": policy.delivery.base_branch,
            "branch_prefix": policy.delivery.branch_prefix,
            "merge_strategy": policy.delivery.merge_strategy,
            "pr_sync_mode": policy.delivery.pr_sync_mode,
            "auto_close_issue": policy.delivery.auto_close_issue,
        },
    }
    if policy.oauth_client_id:
        value["oauth_client_id"] = policy.oauth_client_id
    if policy.oauth_redirect_uri:
        value["oauth_redirect_uri"] = policy.oauth_redirect_uri
    if policy.browser_capture_base_url:
        value["browser_capture_base_url"] = policy.browser_capture_base_url
    if policy.targets:
        value["targets"] = {
            name: {
                "authorization_domain": target.authorization_domain,
                "project": target.project,
                "oauth_client_id": target.oauth_client_id,
                "oauth_redirect_uri": target.oauth_redirect_uri,
                "auth_mode": target.auth_mode,
            }
            for name, target in policy.targets.items()
        }
        value["default_publication_mode"] = policy.default_publication_mode
    for document in updated:
        if document.get("kind") == "ZfConfig":
            target = document.get("spec")
        elif "version" in document and "project" in document:
            target = document
        else:
            continue
        if not isinstance(target, dict):
            raise ValueError("generated ZfConfig spec must be a mapping")
        target["self_issue"] = value
        return updated
    raise ValueError("generated project documents do not contain ZfConfig")


__all__ = [
    "inherit_workspace_self_issue_config",
    "inject_self_issue_policy",
    "locked_self_issue_policy",
]
