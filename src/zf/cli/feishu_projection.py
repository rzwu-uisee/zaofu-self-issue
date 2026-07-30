"""CLI entrypoint for the managed Feishu Kanban projector."""

from __future__ import annotations

import argparse
import os
import sys
import time

from zf.core.config.loader import ConfigError
from zf.core.config.project_context import load_project_env, resolve_project_context
from zf.core.events import EventWriter
from zf.core.events.factory import event_log_from_project
from zf.core.state.locks import FileLock
from zf.core.workspace import stable_project_id
from zf.integrations.feishu.kanban_projector import FeishuKanbanProjector
from zf.integrations.feishu.lark_cli import LarkCliBitableClient
from zf.integrations.feishu.projection_target import (
    FeishuKanbanTargetStore,
    redact_target_token,
    redact_target_url,
    resolve_or_create_kanban_target,
)
from zf.integrations.feishu.transport import FeishuTransportError


def run_project_kanban(args: argparse.Namespace) -> int:
    try:
        context = resolve_project_context(
            explicit_state_dir=getattr(args, "state_dir", None),
            require_config=True,
        )
        load_project_env(context.project_root)
        projection = context.config.runtime.feishu_projection
        backend = str(getattr(args, "backend", "") or projection.backend).strip()
        if backend != "lark-cli":
            raise ConfigError(f"unsupported Feishu projection backend: {backend}")
        writer = EventWriter(
            event_log_from_project(context.state_dir, config=context.config)
        )
        client = LarkCliBitableClient()
        create_target = bool(
            getattr(args, "create_target_if_missing", False)
            or projection.auto_create_target
        )
        explicit_app = str(getattr(args, "app_token", "") or "").strip()
        explicit_table = str(getattr(args, "table_id", "") or "").strip()
        stored = FeishuKanbanTargetStore.for_state_dir(context.state_dir).read()
        if explicit_app or explicit_table or stored is not None or create_target:
            app_token = explicit_app
            table_id = explicit_table
        else:
            app_token = os.environ.get("FEISHU_BITABLE_APP_TOKEN", "").strip()
            table_id = os.environ.get("FEISHU_BITABLE_TABLE_ID", "").strip()
        target_result = resolve_or_create_kanban_target(
            state_dir=context.state_dir,
            project_name=context.config.project.name,
            client=client,
            app_token=app_token,
            table_id=table_id,
            create_if_missing=create_target,
            folder_token=(
                str(getattr(args, "folder_token", "") or "").strip()
                or os.environ.get("FEISHU_FOLDER_TOKEN", "").strip()
            ),
            base_name=(
                str(getattr(args, "base_name", "") or "").strip()
                or projection.base_name
            ),
            table_name=(
                str(getattr(args, "table_name", "") or "").strip()
                or projection.table_name
            ),
            time_zone=(
                str(getattr(args, "timezone", "") or "").strip()
                or projection.time_zone
            ),
        )
        app_token = target_result.target.app_token
        table_id = target_result.target.table_id
        if target_result.created:
            writer.emit(
                "feishu.kanban_projection.target_created",
                actor="zf-feishu-projector",
                payload={
                    "schema_version": "feishu-kanban-projection.v1",
                    "backend": "lark-cli",
                    "app_token": redact_target_token(app_token),
                    "table_id": table_id,
                    "base_url": redact_target_url(
                        target_result.target.base_url,
                        app_token,
                    ),
                    "fields_created": target_result.fields_created,
                    "views_created": target_result.views_created,
                    "views_configured": target_result.views_configured,
                },
            )
        projector = FeishuKanbanProjector(
            state_dir=context.state_dir,
            project_id=stable_project_id(
                name=context.config.project.name,
                root=context.project_root,
            ),
            project_name=context.config.project.name,
            app_token=app_token,
            table_id=table_id,
            client=client,
            writer=writer,
            include_archive_days=projection.include_archive_days,
            reconcile_interval_seconds=projection.reconcile_interval_seconds,
            max_actions_per_tick=projection.max_actions_per_tick,
        )
    except (ConfigError, OSError, ValueError, FeishuTransportError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    owner_lock = FileLock(
        context.state_dir / "integrations" / "feishu" / "kanban-projector-owner",
        timeout_seconds=0.0,
    )
    try:
        owner_lock.__enter__()
    except TimeoutError:
        print(
            "Error: another Feishu Kanban projector owns this project",
            file=sys.stderr,
        )
        return 1

    watch = bool(getattr(args, "watch", False))
    interval = float(
        getattr(args, "poll_interval_seconds", 0.0) or projection.poll_interval_seconds
    )
    force_reconcile = bool(getattr(args, "reconcile", False))
    try:
        writer.emit(
            "feishu.kanban_projection.started",
            actor="zf-feishu-projector",
            payload={
                "schema_version": "feishu-kanban-projection.v1",
                "backend": backend,
                "watch": watch,
                "poll_interval_seconds": interval,
            },
        )
        while True:
            result = projector.tick(force_reconcile=force_reconcile)
            force_reconcile = False
            if not watch:
                print(
                    "Feishu Kanban projector: "
                    f"ok={result['ok']} processed={result['processed']} "
                    f"pending={result['pending']} "
                    f"reconciled={result['reconciled']}"
                )
                return 0 if result["ok"] else 1
            time.sleep(max(0.25, interval))
    except KeyboardInterrupt:
        return 0
    finally:
        try:
            writer.emit(
                "feishu.kanban_projection.stopped",
                actor="zf-feishu-projector",
                payload={
                    "schema_version": "feishu-kanban-projection.v1",
                    "backend": backend,
                },
            )
        finally:
            owner_lock.__exit__(None, None, None)
