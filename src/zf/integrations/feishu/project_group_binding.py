"""Project-owned Feishu collaboration-group bindings.

``zf.yaml`` declares an opt-in desired topology.  This module stores the
resolved Feishu chat and independently verified membership under the project's
configured runtime state directory.  The workspace registry remains only a
project locator; it never becomes a second source of project or chat truth.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping

from zf.core.events import EventWriter
from zf.core.events.factory import event_log_from_project
from zf.core.state.atomic_io import atomic_write_text
from zf.core.state.locks import locked_path
from zf.integrations.feishu.bot_credentials import (
    FeishuBotCredential,
    credential_for_purpose,
)
from zf.integrations.feishu.lark_cli import (
    LarkCliChatAdminClient,
    LarkCliRunner,
)
from zf.integrations.feishu.transport import FeishuTransportError


_BINDING_VERSION = 1
_ACTIVE = "active"
_PENDING = "pending"
_PROVISIONING = "provisioning"
_REPAIR_REQUIRED = "repair_required"


@dataclass(frozen=True)
class FeishuProjectGroupBotBinding:
    purpose: str
    app_id: str
    target: str
    default_member: str
    membership_status: str = "pending"


@dataclass(frozen=True)
class ProjectFeishuGroupBinding:
    binding_id: str
    workspace_id: str
    project_id: str
    group_kind: str
    display_name: str
    status: str
    chat_id: str
    owner_open_id: str
    owner_open_id_env: str
    provisioner_purpose: str
    primary_responder: str
    channel_id: str
    bots: tuple[FeishuProjectGroupBotBinding, ...]
    config_digest: str
    error: str = ""
    created_at: str = ""
    updated_at: str = ""

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "ProjectFeishuGroupBinding":
        raw_bots = raw.get("bots") or []
        bots: list[FeishuProjectGroupBotBinding] = []
        for item in raw_bots:
            if not isinstance(item, Mapping):
                continue
            bots.append(
                FeishuProjectGroupBotBinding(
                    purpose=str(item.get("purpose") or ""),
                    app_id=str(item.get("app_id") or ""),
                    target=str(item.get("target") or ""),
                    default_member=str(item.get("default_member") or ""),
                    membership_status=str(item.get("membership_status") or "pending"),
                )
            )
        return cls(
            binding_id=str(raw.get("binding_id") or ""),
            workspace_id=str(raw.get("workspace_id") or "default"),
            project_id=str(raw.get("project_id") or ""),
            group_kind=str(raw.get("group_kind") or "collaboration"),
            display_name=str(raw.get("display_name") or ""),
            status=str(raw.get("status") or _PENDING),
            chat_id=str(raw.get("chat_id") or ""),
            owner_open_id=str(raw.get("owner_open_id") or ""),
            owner_open_id_env=str(raw.get("owner_open_id_env") or ""),
            provisioner_purpose=str(raw.get("provisioner_purpose") or ""),
            primary_responder=str(raw.get("primary_responder") or ""),
            channel_id=str(raw.get("channel_id") or ""),
            bots=tuple(bots),
            config_digest=str(raw.get("config_digest") or ""),
            error=str(raw.get("error") or ""),
            created_at=str(raw.get("created_at") or ""),
            updated_at=str(raw.get("updated_at") or ""),
        )

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["bots"] = [asdict(bot) for bot in self.bots]
        return data

    def bot_for_app(self, app_id: str) -> FeishuProjectGroupBotBinding | None:
        normalized = str(app_id or "").strip()
        return next((bot for bot in self.bots if bot.app_id == normalized), None)


class ProjectFeishuGroupBindingStore:
    """Atomic project runtime sidecar for resolved Feishu group bindings."""

    def __init__(self, state_dir: Path) -> None:
        self.path = (
            Path(state_dir) / "integrations" / "feishu" / "project_group_bindings.json"
        )

    def list(self) -> list[ProjectFeishuGroupBinding]:
        data = self._read()
        bindings = data.get("bindings") or {}
        if not isinstance(bindings, Mapping):
            return []
        return [
            ProjectFeishuGroupBinding.from_dict(raw)
            for raw in bindings.values()
            if isinstance(raw, Mapping)
        ]

    def get(self, binding_id: str) -> ProjectFeishuGroupBinding | None:
        for binding in self.list():
            if binding.binding_id == binding_id:
                return binding
        return None

    def upsert(self, binding: ProjectFeishuGroupBinding) -> ProjectFeishuGroupBinding:
        if not binding.binding_id:
            raise ValueError("binding_id is required")
        with locked_path(self.path):
            data = self._read()
            raw_bindings = data.get("bindings")
            bindings = dict(raw_bindings) if isinstance(raw_bindings, Mapping) else {}
            bindings[binding.binding_id] = binding.to_dict()
            atomic_write_text(
                self.path,
                json.dumps(
                    {
                        "version": _BINDING_VERSION,
                        "bindings": bindings,
                    },
                    ensure_ascii=False,
                    indent=2,
                    sort_keys=True,
                )
                + "\n",
            )
        return binding

    def _read(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"version": _BINDING_VERSION, "bindings": {}}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"invalid Feishu project-group binding store: {self.path}"
            ) from exc
        if not isinstance(data, dict):
            raise ValueError(
                f"invalid Feishu project-group binding store shape: {self.path}"
            )
        return data


def configured_project_group(config: object | None) -> object | None:
    integrations = getattr(config, "integrations", None)
    group = getattr(integrations, "feishu_project_group", None)
    return group if bool(getattr(group, "enabled", False)) else None


def ensure_project_feishu_group_binding(
    context,
    *,
    workspace_id: str,
    project_id: str,
    provision: bool | None = None,
    env: Mapping[str, str] | None = None,
    client_factory: Callable[[FeishuBotCredential], LarkCliChatAdminClient] | None = None,
) -> ProjectFeishuGroupBinding | None:
    """Ensure the durable desired binding; optionally provision and verify it.

    No external call is made unless ``provision`` is true or the project has
    explicitly configured ``auto_provision: true``.  Failures preserve a
    durable ``repair_required`` record and event rather than silently falling
    back to a wildcard route.
    """
    group = configured_project_group(getattr(context, "config", None))
    if group is None:
        return None
    env = env or os.environ
    store = ProjectFeishuGroupBindingStore(context.state_dir)
    existing = store.get(str(group.binding_id))
    desired = _desired_binding(
        context,
        group=group,
        workspace_id=workspace_id,
        project_id=project_id,
        env=env,
        existing=existing,
    )
    changed = existing is None or existing.config_digest != desired.config_digest
    if existing is not None and existing.chat_id:
        desired = replace(
            desired,
            chat_id=existing.chat_id,
            status=existing.status,
            error=existing.error,
            created_at=existing.created_at,
        )
    persisted = store.upsert(desired)
    if changed:
        _emit_binding_event(
            context,
            "feishu.project_group.binding.requested",
            persisted,
            {"auto_provision": bool(getattr(group, "auto_provision", False))},
        )
    should_provision = (
        bool(getattr(group, "auto_provision", False))
        if provision is None
        else bool(provision)
    )
    if not should_provision:
        return persisted
    return provision_project_feishu_group_binding(
        context,
        binding=persisted,
        env=env,
        client_factory=client_factory,
    )


def provision_project_feishu_group_binding(
    context,
    *,
    binding: ProjectFeishuGroupBinding,
    env: Mapping[str, str] | None = None,
    client_factory: Callable[[FeishuBotCredential], LarkCliChatAdminClient] | None = None,
    chat_id: str = "",
) -> ProjectFeishuGroupBinding:
    """Create or attach a chat, then verify owner and every configured bot."""
    env = env or os.environ
    store = ProjectFeishuGroupBindingStore(context.state_dir)
    requested_chat_id = str(chat_id or binding.chat_id).strip()
    owner = str(env.get(binding.owner_open_id_env) or "").strip()
    credentials, errors = _resolve_binding_credentials(binding, env=env)
    if not owner:
        errors.append(f"missing owner open id env: {binding.owner_open_id_env}")
    provisioner = credentials.get(binding.provisioner_purpose)
    if provisioner is None:
        errors.append(
            f"missing provisioner credentials: {binding.provisioner_purpose}"
        )
    if errors:
        return _mark_repair_required(
            context,
            store,
            binding,
            "; ".join(dict.fromkeys(errors)),
        )

    assert provisioner is not None
    # A pending binding can have been created before credentials were supplied.
    # Refresh only the non-secret App IDs immediately before external work so a
    # later explicit provision is not pinned to that stale pending snapshot.
    binding = replace(
        binding,
        bots=tuple(
            replace(bot, app_id=credentials[bot.purpose].app_id)
            for bot in binding.bots
        ),
    )
    _emit_binding_event(
        context,
        "feishu.project_group.provision.requested",
        binding,
        {"chat_id": requested_chat_id, "operation": "attach" if requested_chat_id else "create"},
    )
    pending = replace(
        binding,
        status=_PROVISIONING,
        error="",
        owner_open_id=owner,
        updated_at=_now_iso(),
    )
    store.upsert(pending)
    try:
        client = (client_factory or _default_chat_client)(provisioner)
        bot_app_ids = [credential.app_id for credential in credentials.values()]
        if not requested_chat_id:
            created = client.create_group(
                name=pending.display_name,
                owner_open_id=owner,
                bot_app_ids=bot_app_ids,
                provisioner_app_id=provisioner.app_id,
            )
            requested_chat_id = str(created["chat_id"])
        verified = client.ensure_members(
            requested_chat_id,
            owner_open_id=owner,
            bot_app_ids=bot_app_ids,
        )
    except (FeishuTransportError, OSError, ValueError) as exc:
        return _mark_repair_required(
            context,
            store,
            pending,
            _provision_failure_message(binding, exc),
        )

    statuses = {
        bot.purpose: (
            "active" if bot.app_id in verified["members"]["bots"] else "missing"
        )
        for bot in pending.bots
    }
    bots = tuple(
        replace(bot, membership_status=statuses.get(bot.purpose, "missing"))
        for bot in pending.bots
    )
    if not bool(verified.get("verified", False)):
        details = []
        if verified.get("missing_users"):
            details.append("owner missing after verification")
        if verified.get("missing_bots"):
            details.append("bot member missing after verification")
        return _mark_repair_required(
            context,
            store,
            replace(pending, chat_id=requested_chat_id, bots=bots),
            "; ".join(details) or "membership verification failed",
        )

    active = replace(
        pending,
        chat_id=requested_chat_id,
        status=_ACTIVE,
        bots=bots,
        error="",
        updated_at=_now_iso(),
    )
    store.upsert(active)
    # The workspace route index is derived, but collision detection is a hard
    # inbound safety invariant.  Do not leave a seemingly-active binding that
    # cannot be selected uniquely by the shared App WS bridge.
    try:
        from zf.core.workspace.feishu_binding_index import (
            WorkspaceFeishuBindingConflict,
            WorkspaceFeishuBindingIndex,
        )
        from zf.core.workspace.registry import WorkspaceRegistry

        WorkspaceFeishuBindingIndex(
            WorkspaceRegistry(workspace=active.workspace_id)
        ).rebuild()
    except WorkspaceFeishuBindingConflict as exc:
        return _mark_repair_required(context, store, active, str(exc))
    _emit_binding_event(
        context,
        "feishu.project_group.provisioned",
        active,
        {"operation": "attached" if chat_id else "created"},
    )
    return active


def attach_project_feishu_group(
    context,
    *,
    binding_id: str,
    chat_id: str,
    env: Mapping[str, str] | None = None,
    client_factory: Callable[[FeishuBotCredential], LarkCliChatAdminClient] | None = None,
) -> ProjectFeishuGroupBinding:
    store = ProjectFeishuGroupBindingStore(context.state_dir)
    binding = store.get(binding_id)
    if binding is None:
        raise ValueError(f"unknown Feishu project-group binding: {binding_id}")
    attached = provision_project_feishu_group_binding(
        context,
        binding=binding,
        chat_id=chat_id,
        env=env,
        client_factory=client_factory,
    )
    _emit_binding_event(
        context,
        "feishu.project_group.attached",
        attached,
        {"chat_id": attached.chat_id},
    )
    return attached


def _desired_binding(
    context,
    *,
    group: object,
    workspace_id: str,
    project_id: str,
    env: Mapping[str, str],
    existing: ProjectFeishuGroupBinding | None,
) -> ProjectFeishuGroupBinding:
    project_name = str(
        getattr(getattr(context, "config", None).project, "name", "")
        if getattr(context, "config", None) is not None
        else ""
    ).strip() or context.project_root.name
    display_name = str(getattr(group, "name_template")).format(
        project_name=project_name
    )
    bots: list[FeishuProjectGroupBotBinding] = []
    for purpose in getattr(group, "bot_purposes", []):
        credential = credential_for_purpose(
            str(purpose), env=env, allow_fallback=False
        )
        bots.append(
            FeishuProjectGroupBotBinding(
                purpose=str(purpose),
                app_id=credential.app_id if credential else "",
                target=_target_for_purpose(str(purpose)),
                default_member=_member_for_purpose(str(purpose)),
                membership_status="pending",
            )
        )
    now = _now_iso()
    config_digest = _binding_digest(
        workspace_id=workspace_id,
        project_id=project_id,
        group=group,
        display_name=display_name,
        bots=bots,
    )
    return ProjectFeishuGroupBinding(
        binding_id=str(getattr(group, "binding_id")),
        workspace_id=str(workspace_id),
        project_id=str(project_id),
        group_kind=str(getattr(group, "group_kind")),
        display_name=display_name,
        status=_PENDING,
        chat_id="",
        owner_open_id="",
        owner_open_id_env=str(getattr(group, "owner_open_id_env")),
        provisioner_purpose=str(getattr(group, "provisioner_purpose")),
        primary_responder=str(getattr(group, "primary_responder")),
        channel_id=str(getattr(group, "channel_id")),
        bots=tuple(bots),
        config_digest=config_digest,
        created_at=existing.created_at if existing else now,
        updated_at=now,
    )


def _resolve_binding_credentials(
    binding: ProjectFeishuGroupBinding,
    *,
    env: Mapping[str, str],
) -> tuple[dict[str, FeishuBotCredential], list[str]]:
    credentials: dict[str, FeishuBotCredential] = {}
    errors: list[str] = []
    for bot in binding.bots:
        credential = credential_for_purpose(
            bot.purpose, env=env, allow_fallback=False
        )
        if credential is None:
            errors.append(f"missing bot credentials: {bot.purpose}")
            continue
        if credential.app_id in {item.app_id for item in credentials.values()}:
            errors.append(
                "each project-group purpose must resolve to a distinct Feishu "
                f"app id; duplicate for {bot.purpose}"
            )
            continue
        credentials[bot.purpose] = credential
    return credentials, errors


def _default_chat_client(credential: FeishuBotCredential) -> LarkCliChatAdminClient:
    child_env = dict(os.environ)
    child_env["FEISHU_APP_ID"] = credential.app_id
    child_env["FEISHU_APP_SECRET"] = credential.app_secret
    return LarkCliChatAdminClient(LarkCliRunner(environ=child_env))


def _mark_repair_required(
    context,
    store: ProjectFeishuGroupBindingStore,
    binding: ProjectFeishuGroupBinding,
    error: str,
) -> ProjectFeishuGroupBinding:
    repaired = replace(
        binding,
        status=_REPAIR_REQUIRED,
        error=str(error)[:800],
        updated_at=_now_iso(),
    )
    store.upsert(repaired)
    _emit_binding_event(
        context,
        "feishu.project_group.repair_required",
        repaired,
        {"error": repaired.error},
    )
    return repaired


def _provision_failure_message(
    binding: ProjectFeishuGroupBinding,
    exc: Exception,
) -> str:
    """Turn known provider setup failures into operator-actionable repair state."""
    raw = str(exc)
    normalized = raw.lower()
    if "open_id cross app" in normalized:
        return (
            f"owner open_id is not visible to provisioner "
            f"{binding.provisioner_purpose}; configure "
            f"{binding.owner_open_id_env} with that Feishu app's open_id"
        )
    if "app_scope_not_applied" in normalized:
        return (
            f"provisioner {binding.provisioner_purpose} has not applied for "
            "required Feishu group scopes: im:chat:create, "
            "im:chat.members:read, im:chat.members:write_only"
        )
    return raw


def _emit_binding_event(
    context,
    event_type: str,
    binding: ProjectFeishuGroupBinding,
    extra: Mapping[str, Any] | None = None,
) -> None:
    payload = {
        "binding_id": binding.binding_id,
        "workspace_id": binding.workspace_id,
        "project_id": binding.project_id,
        "group_kind": binding.group_kind,
        "status": binding.status,
        "chat_id": binding.chat_id,
        "bot_purposes": [bot.purpose for bot in binding.bots],
    }
    if extra:
        payload.update(extra)
    EventWriter(
        event_log_from_project(context.state_dir, config=context.config),
        default_origin="external",
    ).emit(
        event_type,
        actor="feishu-project-group",
        correlation_id=f"feishu-group:{binding.binding_id}",
        payload=payload,
    )


def _binding_digest(
    *,
    workspace_id: str,
    project_id: str,
    group: object,
    display_name: str,
    bots: list[FeishuProjectGroupBotBinding],
) -> str:
    material = {
        "workspace_id": workspace_id,
        "project_id": project_id,
        "binding_id": str(getattr(group, "binding_id")),
        "group_kind": str(getattr(group, "group_kind")),
        "display_name": display_name,
        "owner_open_id_env": str(getattr(group, "owner_open_id_env")),
        "provisioner_purpose": str(getattr(group, "provisioner_purpose")),
        "primary_responder": str(getattr(group, "primary_responder")),
        "channel_id": str(getattr(group, "channel_id")),
        "bots": [asdict(bot) for bot in bots],
    }
    return hashlib.sha256(
        json.dumps(material, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _target_for_purpose(purpose: str) -> str:
    if purpose == "run_manager":
        return "run_manager"
    if purpose == "kanban_agent":
        return "kanban_agent"
    return "channel"


def _member_for_purpose(purpose: str) -> str:
    if purpose == "run_manager":
        return "run-manager"
    if purpose == "kanban_agent":
        return "zf-product-manager"
    return ""


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
