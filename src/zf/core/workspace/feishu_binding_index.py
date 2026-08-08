"""Derived workspace index for exact Feishu ``(app_id, chat_id)`` routing.

The index is rebuildable metadata under the workspace home.  It has no task,
event, or project state of its own: each record is derived from a registered
project's active ``ProjectFeishuGroupBinding`` sidecar.  Duplicate keys are a
hard error because a shared Feishu App otherwise load-balances a message into
an ambiguous project.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from zf.core.config.loader import ConfigError
from zf.core.config.schema import FeishuRouteConfig
from zf.core.state.atomic_io import atomic_write_text
from zf.core.state.locks import locked_path
from zf.core.workspace.project_resolver import ProjectResolver
from zf.core.workspace.registry import WorkspaceRegistry, workspace_home
from zf.integrations.feishu.project_group_binding import (
    ProjectFeishuGroupBinding,
    ProjectFeishuGroupBindingStore,
)


_INDEX_VERSION = 1


class WorkspaceFeishuBindingConflict(RuntimeError):
    """Two active projects claim the same inbound Feishu app/chat pair."""


class ProviderFeishuBindingConflict(WorkspaceFeishuBindingConflict):
    """Two workspaces claim the same inbound Feishu app/chat pair."""


@dataclass(frozen=True)
class WorkspaceFeishuRoute:
    app_id: str
    chat_id: str
    workspace_id: str
    project_id: str
    binding_id: str
    purpose: str
    target: str
    channel_id: str
    default_member: str

    @property
    def key(self) -> str:
        return f"{self.app_id}:{self.chat_id}"

    def to_feishu_route(self) -> FeishuRouteConfig:
        return FeishuRouteConfig(
            target=self.target,
            channel_id=self.channel_id,
            default_member=self.default_member,
        )

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "WorkspaceFeishuRoute":
        return cls(
            app_id=str(raw.get("app_id") or ""),
            chat_id=str(raw.get("chat_id") or ""),
            workspace_id=str(raw.get("workspace_id") or "default"),
            project_id=str(raw.get("project_id") or ""),
            binding_id=str(raw.get("binding_id") or ""),
            purpose=str(raw.get("purpose") or ""),
            target=str(raw.get("target") or ""),
            channel_id=str(raw.get("channel_id") or ""),
            default_member=str(raw.get("default_member") or ""),
        )


@dataclass(frozen=True)
class WorkspaceFeishuInboundResolution:
    context: object
    route: FeishuRouteConfig
    binding: ProjectFeishuGroupBinding
    index_route: WorkspaceFeishuRoute


class WorkspaceFeishuBindingIndex:
    def __init__(self, registry: WorkspaceRegistry | None = None) -> None:
        self.registry = registry or WorkspaceRegistry()
        self.path = self.registry.path.parent / "feishu_route_index.json"

    def read(self) -> dict[str, WorkspaceFeishuRoute]:
        if not self.path.exists():
            return {}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid workspace Feishu route index: {self.path}") from exc
        raw_routes = data.get("routes") if isinstance(data, dict) else None
        if not isinstance(raw_routes, Mapping):
            return {}
        routes: dict[str, WorkspaceFeishuRoute] = {}
        for key, raw in raw_routes.items():
            if not isinstance(raw, Mapping):
                continue
            route = WorkspaceFeishuRoute.from_dict(raw)
            if route.key != str(key) or not route.app_id or not route.chat_id:
                continue
            routes[route.key] = route
        return routes

    def lookup(self, app_id: str, chat_id: str) -> WorkspaceFeishuRoute | None:
        key = f"{str(app_id or '').strip()}:{str(chat_id or '').strip()}"
        return self.read().get(key)

    def rebuild(self) -> dict[str, WorkspaceFeishuRoute]:
        resolver = ProjectResolver(self.registry)
        routes: dict[str, WorkspaceFeishuRoute] = {}
        for project in self.registry.list_projects():
            try:
                resolution = resolver.resolve(project.project_id)
            except (ConfigError, FileNotFoundError, ValueError):
                # A stale registry descriptor cannot own inbound traffic.  The
                # next valid project still rebuilds normally.
                continue
            for binding in ProjectFeishuGroupBindingStore(
                resolution.context.state_dir
            ).list():
                if (
                    binding.workspace_id != self.registry.workspace
                    or binding.project_id != project.project_id
                    or binding.status != "active"
                    or not binding.chat_id
                ):
                    continue
                for bot in binding.bots:
                    if not bot.app_id or bot.membership_status != "active":
                        continue
                    route = WorkspaceFeishuRoute(
                        app_id=bot.app_id,
                        chat_id=binding.chat_id,
                        workspace_id=binding.workspace_id,
                        project_id=binding.project_id,
                        binding_id=binding.binding_id,
                        purpose=bot.purpose,
                        target=bot.target,
                        channel_id=binding.channel_id if bot.target == "channel" else "",
                        default_member=bot.default_member,
                    )
                    existing = routes.get(route.key)
                    if existing is not None and existing != route:
                        raise WorkspaceFeishuBindingConflict(
                            "duplicate active Feishu project-group route "
                            f"{route.key}: {existing.project_id}/{existing.binding_id} "
                            f"and {route.project_id}/{route.binding_id}"
                        )
                    routes[route.key] = route
        payload = {
            "version": _INDEX_VERSION,
            "workspace_id": self.registry.workspace,
            "updated_at": _now_iso(),
            "routes": {key: asdict(route) for key, route in sorted(routes.items())},
        }
        with locked_path(self.path):
            atomic_write_text(
                self.path,
                json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            )
        return routes


class WorkspaceFeishuInboundResolver:
    """Resolve exactly one active project binding for a workspace WS event."""

    def __init__(self, *, workspace: str = "default", registry: WorkspaceRegistry | None = None) -> None:
        self.registry = registry or WorkspaceRegistry(workspace=workspace)
        self.index = WorkspaceFeishuBindingIndex(self.registry)
        self.resolver = ProjectResolver(self.registry)

    def refresh(self) -> dict[str, WorkspaceFeishuRoute]:
        return self.index.rebuild()

    def resolve(self, *, app_id: str, chat_id: str) -> WorkspaceFeishuInboundResolution | None:
        route = self.index.lookup(app_id, chat_id)
        if route is None:
            return None
        resolution = self.resolver.resolve(route.project_id)
        binding = ProjectFeishuGroupBindingStore(
            resolution.context.state_dir
        ).get(route.binding_id)
        # Revalidate the live sidecar after the derived index lookup.  A stale
        # index must fail closed rather than dispatching into a removed project.
        if (
            binding is None
            or binding.status != "active"
            or binding.workspace_id != self.registry.workspace
            or binding.project_id != route.project_id
            or binding.chat_id != route.chat_id
        ):
            return None
        bot = binding.bot_for_app(route.app_id)
        if bot is None or bot.purpose != route.purpose or bot.membership_status != "active":
            return None
        return WorkspaceFeishuInboundResolution(
            context=resolution.context,
            route=route.to_feishu_route(),
            binding=binding,
            index_route=route,
        )


class ProviderFeishuInboundResolver:
    """Resolve an inbound Feishu App/chat pair across all local workspaces.

    A Feishu App permits only one effective long-connection owner on a host:
    Feishu load-balances events across parallel connections.  That bridge must
    route the received event into the correct project without collapsing the
    canonical project state into a provider-global store.  This resolver holds
    only derived route metadata; the selected workspace resolver revalidates
    the project binding before dispatch.
    """

    def __init__(self, *, home: Path | None = None) -> None:
        self.home = workspace_home(home)
        self._routes: dict[str, WorkspaceFeishuRoute] = {}
        self._resolvers: dict[str, WorkspaceFeishuInboundResolver] = {}

    def refresh(self) -> dict[str, WorkspaceFeishuRoute]:
        routes: dict[str, WorkspaceFeishuRoute] = {}
        resolvers: dict[str, WorkspaceFeishuInboundResolver] = {}
        workspaces_root = self.home / "workspaces"
        if not workspaces_root.exists():
            self._routes = {}
            self._resolvers = {}
            return {}
        for projects_path in sorted(workspaces_root.glob("*/projects.json")):
            registry = WorkspaceRegistry(
                workspace=projects_path.parent.name,
                path=projects_path,
            )
            resolver = WorkspaceFeishuInboundResolver(registry=registry)
            workspace_routes = resolver.refresh()
            resolvers[registry.workspace] = resolver
            for key, route in workspace_routes.items():
                existing = routes.get(key)
                if existing is not None and existing != route:
                    raise ProviderFeishuBindingConflict(
                        "duplicate active Feishu provider route "
                        f"{key}: {existing.workspace_id}/{existing.project_id}/"
                        f"{existing.binding_id} and {route.workspace_id}/"
                        f"{route.project_id}/{route.binding_id}"
                    )
                routes[key] = route
        self._routes = routes
        self._resolvers = resolvers
        return dict(routes)

    def routes_for_app(self, app_id: str) -> list[WorkspaceFeishuRoute]:
        normalized_app_id = str(app_id or "").strip()
        return [
            route for route in self._routes.values()
            if route.app_id == normalized_app_id
        ]

    def resolve(self, *, app_id: str, chat_id: str) -> WorkspaceFeishuInboundResolution | None:
        key = f"{str(app_id or '').strip()}:{str(chat_id or '').strip()}"
        route = self._routes.get(key)
        if route is None:
            # Bindings can be provisioned after the provider bridge starts.
            # Rebuild only on an unknown exact route, keeping the hot path local.
            self.refresh()
            route = self._routes.get(key)
        if route is None:
            return None
        resolver = self._resolvers.get(route.workspace_id)
        if resolver is None:
            self.refresh()
            resolver = self._resolvers.get(route.workspace_id)
        if resolver is None:
            return None
        resolved = resolver.resolve(app_id=app_id, chat_id=chat_id)
        if resolved is not None:
            return resolved
        # A binding may have moved or been repaired with the same App/chat key
        # after this provider bridge started.  One rebuild lets the new exact
        # route take effect without a bridge restart; a still-invalid binding
        # remains fail-closed.
        self.refresh()
        refreshed_route = self._routes.get(key)
        if refreshed_route is None:
            return None
        refreshed_resolver = self._resolvers.get(refreshed_route.workspace_id)
        if refreshed_resolver is None:
            return None
        return refreshed_resolver.resolve(app_id=app_id, chat_id=chat_id)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
