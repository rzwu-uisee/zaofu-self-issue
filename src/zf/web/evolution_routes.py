"""Read-only Web projection for evidence-bound self-evolution."""

from __future__ import annotations

from typing import Any, Callable

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from zf.runtime.evolution_coordinator import EvolutionCoordinator


def build_evolution_router(*, resolve_ctx: Callable[[str], Any]) -> APIRouter:
    router = APIRouter()

    @router.get("/api/projects/{project_id}/evolution")
    def evolution_projection(project_id: str) -> JSONResponse:
        context = resolve_ctx(project_id)
        projection = EvolutionCoordinator(context.state_dir).projection()
        projection["project_id"] = project_id
        projection["authority"] = {
            "events": "events.jsonl",
            "current_state": "evolution stores",
            "semantic_bodies": "immutable artifact refs",
            "projection_only": True,
        }
        return JSONResponse(projection)

    return router


__all__ = ["build_evolution_router"]
