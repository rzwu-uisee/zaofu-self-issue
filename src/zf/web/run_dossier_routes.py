"""Read-only Web routes for cached Goal run dossiers."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse

from zf.core.config.schema import ZfConfig
from zf.runtime.goal_dossier import (
    GoalDossierError,
    build_cached_goal_dossier,
    goal_dossier_view,
)


def build_run_dossier_router(
    *,
    default_project_id: str,
    default_state_dir: Path,
    default_config: ZfConfig | None,
    default_project_root: Path,
    resolve_project: Callable[..., Any],
) -> APIRouter:
    router = APIRouter()

    @router.get("/api/projects/{project_id}/runs/{run_id}/dossier")
    def project_run_dossier(
        project_id: str,
        run_id: str,
        section: str = "",
        preview: bool = False,
    ) -> JSONResponse:
        context = resolve_project(
            project_id,
            default_project_id=default_project_id,
            default_state_dir=default_state_dir,
            default_config=default_config,
            default_project_root=default_project_root,
        )
        try:
            dossier = build_cached_goal_dossier(
                context.state_dir,
                run_id,
                project_root=context.project_root,
                config=context.config,
            )
            return JSONResponse(goal_dossier_view(
                dossier,
                section=section,
                preview=preview,
            ))
        except GoalDossierError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    return router


__all__ = ["build_run_dossier_router"]
