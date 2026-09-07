"""Measure Loop Web API routes."""

from __future__ import annotations

import threading
from datetime import datetime, timezone
from typing import Any, Callable

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from zf.runtime.dispatch_diagnostics import build_dispatch_diagnostics
from zf.runtime.loop_projection import build_loop_projection
from zf.runtime.measure_loop_projection import build_measure_loop_projection
from zf.runtime.stage_loop_projection import (
    LOOP_VIEW_CACHE_REVISION,
    build_loop_view,
)
from zf.web.projections.loop_view_source import (
    LOOP_VIEW_SOURCE_FIELD,
    loop_view_source_fingerprint,
    read_stable_loop_view_source,
)

MEASURE_LOOP_SOURCE_FIELD = "_measure_loop_source_fingerprint"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def build_measure_loop_router(*, resolve_ctx: Callable[[str], Any]) -> APIRouter:
    router = APIRouter()
    # The SQLite projection cache is durable but every hit still deserializes
    # a potentially large JSON blob. Keep the latest verified response in the
    # app process as well; the source fingerprint remains the freshness
    # authority, so event/sidecar changes invalidate it immediately.
    measure_cache: dict[str, tuple[str, dict[str, Any]]] = {}
    loop_view_cache: dict[str, tuple[str, dict[str, Any]]] = {}
    cache_lock = threading.Lock()

    @router.get("/api/projects/{project_id}/measure/loops")
    def measure_loops(project_id: str, feature_id: str = "", lens: str = "all") -> JSONResponse:
        ctx = resolve_ctx(project_id)
        source_seq = 0
        cache_key = f"measure-loop:{project_id}:{feature_id or '-'}:{lens or 'all'}"
        source_fingerprint = loop_view_source_fingerprint(
            ctx.state_dir,
            config=ctx.config,
            project_root=ctx.project_root,
        )
        if source_fingerprint:
            with cache_lock:
                cached_local = measure_cache.get(cache_key)
            if cached_local is not None and cached_local[0] == source_fingerprint:
                return JSONResponse(cached_local[1])
        try:
            from zf.web.projections import read_model

            source_seq = read_model.current_projected_seq(ctx.state_dir, config=ctx.config)
            cached = read_model.get_cached_projection(
                ctx.state_dir,
                cache_key,
                source_seq=source_seq,
            )
            if cached is not None:
                cached_payload = dict(cached)
                cached_fingerprint = str(cached_payload.pop(MEASURE_LOOP_SOURCE_FIELD, ""))
                if not source_fingerprint or cached_fingerprint == source_fingerprint:
                    if source_fingerprint:
                        with cache_lock:
                            measure_cache[cache_key] = (source_fingerprint, cached_payload)
                            while len(measure_cache) > 128:
                                measure_cache.pop(next(iter(measure_cache)))
                    return JSONResponse(cached_payload)
        except Exception:
            source_seq = 0
        # Reuse the shared decoded EventLog cache.  The previous direct
        # ``read_all`` bypassed it, so each Delivery/Loop navigation hydrated
        # the complete history again even when the source was unchanged.
        from zf.web.projections.events import _events_with_seq

        events = _events_with_seq(ctx.state_dir, config=ctx.config)
        generated_at = _now()
        loop_projection = build_loop_projection(events=events, generated_at=generated_at, project_id=project_id)
        dispatch = build_dispatch_diagnostics(
            ctx.state_dir,
            config=ctx.config,
            project_root=ctx.project_root,
        )
        projection = build_measure_loop_projection(
            ctx.state_dir,
            config=ctx.config,
            project_root=ctx.project_root,
            project_id=project_id,
            feature_id=feature_id,
            lens=lens,
            generated_at=generated_at,
            events=events,
            loop_projection=loop_projection,
            dispatch_diagnostics=dispatch,
        )
        if source_seq:
            try:
                from zf.web.projections import read_model

                read_model.set_cached_projection(
                    ctx.state_dir,
                    cache_key,
                    kind="measure-loop",
                    source_seq=source_seq,
                    payload={
                        **projection,
                        MEASURE_LOOP_SOURCE_FIELD: source_fingerprint,
                    },
                )
            except Exception:
                pass
        if source_fingerprint:
            with cache_lock:
                measure_cache[cache_key] = (source_fingerprint, projection)
                while len(measure_cache) > 128:
                    measure_cache.pop(next(iter(measure_cache)))
        return JSONResponse(projection)

    @router.get("/api/projects/{project_id}/loop-view")
    def loop_view(project_id: str) -> JSONResponse:
        ctx = resolve_ctx(project_id)
        cache_key = f"loop-view:{LOOP_VIEW_CACHE_REVISION}:{project_id}"
        source_fingerprint = loop_view_source_fingerprint(
            ctx.state_dir,
            config=ctx.config,
            project_root=ctx.project_root,
        )
        if source_fingerprint:
            with cache_lock:
                cached_local = loop_view_cache.get(cache_key)
            if cached_local is not None and cached_local[0] == source_fingerprint:
                return JSONResponse(cached_local[1])
        if source_fingerprint:
            try:
                from zf.web.projections import read_model

                cached = read_model.get_cached_projection(ctx.state_dir, cache_key)
                if cached is not None:
                    cached_payload = dict(cached)
                    cached_fingerprint = str(cached_payload.pop(LOOP_VIEW_SOURCE_FIELD, ""))
                    verified_fingerprint = loop_view_source_fingerprint(
                        ctx.state_dir,
                        config=ctx.config,
                        project_root=ctx.project_root,
                    )
                    if (
                        cached_fingerprint == source_fingerprint
                        and verified_fingerprint == source_fingerprint
                    ):
                        with cache_lock:
                            loop_view_cache[cache_key] = (source_fingerprint, cached_payload)
                            while len(loop_view_cache) > 128:
                                loop_view_cache.pop(next(iter(loop_view_cache)))
                        return JSONResponse(cached_payload)
            except Exception:
                pass
        source = read_stable_loop_view_source(
            ctx.state_dir,
            config=ctx.config,
            project_root=ctx.project_root,
        )
        projection = build_loop_view(
            ctx.state_dir,
            config=ctx.config,
            project_root=ctx.project_root,
            project_id=project_id,
            events=source.events,
            generated_at=_now(),
        )
        if source.cacheable:
            try:
                from zf.web.projections import read_model

                if loop_view_source_fingerprint(
                    ctx.state_dir,
                    config=ctx.config,
                    project_root=ctx.project_root,
                    ) == source.fingerprint:
                    cached_projection = {
                        **projection,
                        "projection_cache": {
                            "key": cache_key,
                            "source": "read_model.sqlite",
                            "source_seq": source.source_seq,
                        },
                    }
                    with cache_lock:
                        loop_view_cache[cache_key] = (source.fingerprint, cached_projection)
                        while len(loop_view_cache) > 128:
                            loop_view_cache.pop(next(iter(loop_view_cache)))
                    read_model.set_cached_projection(
                        ctx.state_dir,
                        cache_key,
                        kind="loop-view",
                        source_seq=source.source_seq,
                        payload={
                            **projection,
                            LOOP_VIEW_SOURCE_FIELD: source.fingerprint,
                        },
                    )
            except Exception:
                pass
        return JSONResponse(projection)

    return router
