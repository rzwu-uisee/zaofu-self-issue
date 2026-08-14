"""Read-only project cost projection routes."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from zf.web.projections.common import _cost as read_cost


def build_cost_router(
    *,
    resolve_ctx: Callable[[str], Any],
    default_state_dir: Path,
) -> APIRouter:
    router = APIRouter()

    @router.get("/api/projects/{project_id}/cost")
    def project_cost(project_id: str) -> JSONResponse:
        return JSONResponse(read_cost(resolve_ctx(project_id).state_dir))

    @router.get("/api/cost")
    def cost() -> JSONResponse:
        return JSONResponse(read_cost(default_state_dir))

    return router


__all__ = ["build_cost_router"]
