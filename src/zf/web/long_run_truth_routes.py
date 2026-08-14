"""Read-only routes for the current long-run workflow truth projection."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from zf.core.config.schema import ZfConfig
from zf.core.events.factory import event_log_from_project
from zf.runtime.long_run_truth import project_long_run_truth


def read_long_run_truth(
    state_dir: Path,
    *,
    config: ZfConfig | None = None,
) -> dict[str, Any]:
    """Project current-run truth from the configured append-only event ledger."""

    event_log = event_log_from_project(state_dir, config=config, warn=False)
    return project_long_run_truth(
        event_log.read_all() if event_log.path.exists() else []
    )


def build_long_run_truth_router(
    *,
    resolve_ctx: Callable[[str], Any],
    default_state_dir: Path,
    default_config: ZfConfig | None,
    default_project_root: Path,
) -> APIRouter:
    """Expose default-project and workspace-project read-only projections."""

    router = APIRouter()

    @router.get("/api/long-run-truth")
    def long_run_truth() -> JSONResponse:
        return JSONResponse(
            read_long_run_truth(default_state_dir, config=default_config)
        )

    @router.get("/api/projects/{project_id}/long-run-truth")
    def project_long_run_truth(project_id: str) -> JSONResponse:
        context = resolve_ctx(project_id)
        return JSONResponse(
            read_long_run_truth(context.state_dir, config=context.config)
        )

    @router.get("/api/run-manager")
    def run_manager_projection() -> JSONResponse:
        from zf.runtime.run_manager import build_run_manager_projection

        return JSONResponse(build_run_manager_projection(
            default_state_dir,
            config=default_config,
            project_root=default_project_root,
        ))

    @router.get("/api/run-contract")
    def run_contract_projection() -> JSONResponse:
        from zf.runtime.delivery_projection import project_run_contract

        return JSONResponse(project_run_contract(default_state_dir))

    @router.get("/api/failure-candidates")
    def failure_candidates_projection() -> JSONResponse:
        from zf.runtime.delivery_projection import project_failure_candidates

        return JSONResponse(project_failure_candidates(default_state_dir))

    @router.get("/api/real-e2e-matrix")
    def real_e2e_matrix_projection() -> JSONResponse:
        from zf.runtime.delivery_projection import project_real_e2e_matrix

        return JSONResponse(project_real_e2e_matrix(
            default_state_dir,
            project_root=default_project_root,
        ))

    @router.get("/api/run-goal")
    def run_goal_projection() -> JSONResponse:
        from zf.runtime.run_manager import build_run_goal_projection

        event_log = event_log_from_project(
            default_state_dir,
            config=default_config,
            warn=False,
        )
        return JSONResponse(build_run_goal_projection(
            event_log.read_all() if event_log.path.exists() else []
        ))

    return router
