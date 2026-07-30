"""Project-scoped Feishu Kanban target bootstrap and persistence."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from zf.core.state.atomic_io import atomic_write_text
from zf.core.state.locks import locked_path
from zf.integrations.feishu.client_ports import BitableClient
from zf.integrations.feishu.targets import (
    kanban_field_specs,
    kanban_view_layout_specs,
    kanban_view_specs,
)


_TARGET_SCHEMA = "feishu-kanban-target.v1"


def redact_target_token(value: str) -> str:
    if len(value) <= 8:
        return "***"
    return f"{value[:4]}...{value[-4:]}"


def redact_target_url(value: str, target_token: str) -> str:
    if not value or not target_token:
        return value
    return value.replace(target_token, redact_target_token(target_token))


@dataclass(frozen=True)
class FeishuKanbanTarget:
    app_token: str
    table_id: str
    base_url: str = ""
    ready: bool = True


@dataclass(frozen=True)
class FeishuKanbanTargetResult:
    target: FeishuKanbanTarget
    created: bool
    fields_created: int = 0
    views_created: int = 0
    views_configured: int = 0


class FeishuKanbanTargetStore:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    @classmethod
    def for_state_dir(cls, state_dir: Path) -> "FeishuKanbanTargetStore":
        return cls(
            Path(state_dir) / "integrations" / "feishu" / "kanban-target.json"
        )

    def read(self) -> FeishuKanbanTarget | None:
        with locked_path(self.path):
            if not self.path.exists():
                return None
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict) or payload.get("schema_version") != _TARGET_SCHEMA:
            raise ValueError("invalid Feishu Kanban target store")
        app_token = str(payload.get("app_token") or "").strip()
        table_id = str(payload.get("table_id") or "").strip()
        if not app_token or not table_id:
            raise ValueError("incomplete Feishu Kanban target store")
        ready = payload.get("ready", True)
        if not isinstance(ready, bool):
            raise ValueError("invalid Feishu Kanban target ready state")
        return FeishuKanbanTarget(
            app_token=app_token,
            table_id=table_id,
            base_url=str(payload.get("base_url") or "").strip(),
            ready=ready,
        )

    def write(self, target: FeishuKanbanTarget) -> None:
        if not target.app_token.strip() or not target.table_id.strip():
            raise ValueError("Feishu Kanban target requires app_token and table_id")
        payload = {
            "schema_version": _TARGET_SCHEMA,
            "app_token": target.app_token.strip(),
            "table_id": target.table_id.strip(),
            "base_url": target.base_url.strip(),
            "ready": target.ready,
        }
        with locked_path(self.path):
            self.path.parent.mkdir(parents=True, exist_ok=True)
            atomic_write_text(
                self.path,
                json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
                + "\n",
            )


def resolve_or_create_kanban_target(
    *,
    state_dir: Path,
    project_name: str,
    client: BitableClient,
    app_token: str = "",
    table_id: str = "",
    create_if_missing: bool = False,
    folder_token: str = "",
    base_name: str = "",
    table_name: str = "Kanban",
    time_zone: str = "Asia/Shanghai",
    field_map: dict[str, str] | None = None,
) -> FeishuKanbanTargetResult:
    app_token = app_token.strip()
    table_id = table_id.strip()
    if bool(app_token) != bool(table_id):
        raise ValueError("Feishu Kanban target requires both app_token and table_id")
    if app_token and table_id:
        return FeishuKanbanTargetResult(
            target=FeishuKanbanTarget(app_token=app_token, table_id=table_id),
            created=False,
        )

    store = FeishuKanbanTargetStore.for_state_dir(state_dir)
    bootstrap_path = store.path.with_name(f"{store.path.name}.bootstrap")
    with locked_path(bootstrap_path):
        stored = store.read()
        if stored is not None:
            if stored.ready:
                return FeishuKanbanTargetResult(target=stored, created=False)
            if not create_if_missing:
                raise ValueError("Feishu Kanban target bootstrap is incomplete")
            return _finish_target_bootstrap(
                store=store,
                target=stored,
                client=client,
                field_map=field_map,
            )
        if not create_if_missing:
            raise ValueError(
                "FEISHU_BITABLE_APP_TOKEN and FEISHU_BITABLE_TABLE_ID are required"
            )
        folder_token = folder_token.strip()
        if not folder_token:
            raise ValueError(
                "FEISHU_FOLDER_TOKEN is required when auto-creating a Kanban target"
            )

        resolved_project_name = project_name.strip() or Path(state_dir).parent.name
        resolved_base_name = (
            base_name.strip() or f"ZaoFu Kanban - {resolved_project_name}"
        )
        resolved_table_name = table_name.strip() or "Kanban"
        base = client.create_base(
            name=resolved_base_name,
            folder_token=folder_token,
            time_zone=time_zone.strip() or "Asia/Shanghai",
        )
        created_app_token = _required_result_value(
            base,
            "app_token",
            "base_token",
        )
        table = client.create_table(created_app_token, name=resolved_table_name)
        target = FeishuKanbanTarget(
            app_token=created_app_token,
            table_id=_required_result_value(table, "table_id", "id"),
            base_url=str(base.get("url") or "").strip(),
            ready=False,
        )
        store.write(target)
        return _finish_target_bootstrap(
            store=store,
            target=target,
            client=client,
            field_map=field_map,
        )


def _finish_target_bootstrap(
    *,
    store: FeishuKanbanTargetStore,
    target: FeishuKanbanTarget,
    client: BitableClient,
    field_map: dict[str, str] | None,
) -> FeishuKanbanTargetResult:
    schema = client.ensure_fields(
        target.app_token,
        target.table_id,
        kanban_field_specs(field_map),
    )
    views = client.ensure_views(
        target.app_token,
        target.table_id,
        kanban_view_specs(),
    )
    layouts = client.ensure_view_layouts(
        target.app_token,
        target.table_id,
        kanban_view_layout_specs(field_map),
    )
    ready_target = FeishuKanbanTarget(
        app_token=target.app_token,
        table_id=target.table_id,
        base_url=target.base_url,
        ready=True,
    )
    store.write(ready_target)
    return FeishuKanbanTargetResult(
        target=ready_target,
        created=True,
        fields_created=len(schema.get("created") or []),
        views_created=len(views.get("created") or []),
        views_configured=len(layouts.get("configured") or []),
    )


def _required_result_value(payload: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = str(payload.get(key) or "").strip()
        if value:
            return value
    raise ValueError(f"Feishu target response is missing {'/'.join(keys)}")
