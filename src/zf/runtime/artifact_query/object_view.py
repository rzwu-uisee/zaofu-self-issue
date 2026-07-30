"""Authorization-aware semantic object views over artifact occurrences."""

from __future__ import annotations

from typing import Any, Iterable, Mapping

from zf.runtime.artifact_access import artifact_access_allowed
from zf.runtime.artifact_query.models import QueryContext


class ArtifactObjectViewMixin:
    def _catalog_object_items(
        self,
        rows: Iterable[Mapping[str, Any]],
        context: QueryContext,
    ) -> list[dict[str, Any]]:
        grouped: dict[str, list[dict[str, Any]]] = {}
        for row in rows:
            visible = self._catalog_visibility(row, context)
            if not visible.get("authorized"):
                continue
            object_id = str(visible.get("object_id") or "")
            if object_id:
                grouped.setdefault(object_id, []).append(visible)
        return [
            self._catalog_object_item(object_rows)
            for object_rows in grouped.values()
        ]

    def _catalog_object_item(
        self,
        rows: list[dict[str, Any]],
    ) -> dict[str, Any]:
        latest = rows[0]
        semantic_kinds = sorted({
            str(row.get("semantic_kind") or "untyped")
            for row in rows
        })
        storage_kinds = sorted({
            str(row.get("storage_kind") or row.get("kind") or "sidecar")
            for row in rows
        })
        lineage = {
            plural: sorted({
                str(row.get(field) or "")
                for row in rows
                if str(row.get(field) or "")
            })
            for plural, field in (
                ("run_ids", "run_id"),
                ("task_ids", "task_id"),
                ("claim_ids", "claim_id"),
                ("stage_ids", "stage_id"),
                ("attempt_ids", "attempt_id"),
                ("operation_ids", "operation_id"),
                ("package_ids", "package_id"),
            )
        }
        locators = {
            str(row.get("locator_id") or "")
            for row in rows
            if str(row.get("locator_id") or "")
        }
        return {
            "authorized": True,
            "object_id": str(latest.get("object_id") or ""),
            "sha256": str(latest.get("sha256") or ""),
            "byte_count": int(latest.get("byte_count") or 0),
            "semantic_kind": (
                semantic_kinds[0]
                if len(semantic_kinds) == 1
                else "mixed"
            ),
            "semantic_kinds": semantic_kinds,
            "storage_kinds": storage_kinds,
            "occurrence_count": len(rows),
            "locator_count": len(locators),
            "latest_locator": self._catalog_locator_item([latest]),
            "lineage": lineage,
        }

    def _catalog_detail(
        self,
        rows: list[dict[str, Any]],
        *,
        matched_by: str,
        context: QueryContext,
    ) -> dict[str, Any] | None:
        if not rows:
            return None
        visible_rows: list[dict[str, Any]] = []
        for row in rows:
            visible = self._catalog_visibility(row, context)
            if visible.get("authorized"):
                visible_rows.append(visible)
        if not visible_rows:
            return {
                "authorized": False,
                "redacted": True,
                "matched_by": matched_by,
            }
        locator_groups: dict[str, list[dict[str, Any]]] = {}
        for row in visible_rows:
            locator_groups.setdefault(
                str(row.get("locator_id") or ""),
                [],
            ).append(row)
        return {
            "authorized": True,
            "matched_by": matched_by,
            "object": self._catalog_object_metadata(visible_rows),
            "locators": [
                self._catalog_locator_item(locator_rows)
                for locator_rows in locator_groups.values()
            ],
            "occurrences": [
                self._catalog_occurrence_item(row)
                for row in visible_rows
            ],
        }

    @staticmethod
    def _catalog_object_metadata(
        rows: list[dict[str, Any]],
    ) -> dict[str, Any]:
        latest = rows[0]
        semantic_kinds = sorted({
            str(row.get("semantic_kind") or "untyped")
            for row in rows
        })
        return {
            "object_id": str(latest.get("object_id") or ""),
            "sha256": str(latest.get("sha256") or ""),
            "byte_count": int(latest.get("byte_count") or 0),
            "semantic_kind": (
                semantic_kinds[0]
                if len(semantic_kinds) == 1
                else "mixed"
            ),
            "semantic_kinds": semantic_kinds,
        }

    @staticmethod
    def _catalog_locator_item(
        rows: list[dict[str, Any]],
    ) -> dict[str, Any]:
        latest = rows[0]
        return {
            "locator_id": str(latest.get("locator_id") or ""),
            "object_id": str(latest.get("object_id") or ""),
            "project_scope": str(latest.get("project_scope") or ""),
            "state_scope": str(latest.get("state_scope") or ""),
            "ref": str(latest.get("ref") or ""),
            "storage_kind": str(
                latest.get("storage_kind")
                or latest.get("kind")
                or "sidecar"
            ),
            "schema_version": str(latest.get("schema_version") or ""),
            "content_type": str(latest.get("content_type") or ""),
            "encoding": str(latest.get("encoding") or ""),
            "health": str(latest.get("health") or "unknown"),
            "semantic_kinds": sorted({
                str(row.get("semantic_kind") or "untyped")
                for row in rows
            }),
        }

    @staticmethod
    def _catalog_occurrence_item(
        row: Mapping[str, Any],
    ) -> dict[str, Any]:
        return {
            key: row.get(key)
            for key in (
                "occurrence_id",
                "locator_id",
                "semantic_kind",
                "event_id",
                "source_event_id",
                "source_seq",
                "source_kind",
                "producer_actor",
                "status",
                "run_id",
                "task_id",
                "claim_id",
                "stage_id",
                "attempt_id",
                "attempt_domain",
                "operation_id",
                "package_id",
                "required",
                "access_scope",
                "retention",
                "created_by",
                "preview",
            )
        }

    @staticmethod
    def _matching_canonical_rows(
        identity: str,
        rows: list[dict[str, Any]],
    ) -> tuple[list[dict[str, Any]], str]:
        identity = str(identity or "").strip()
        for field, matched_by in (
            ("occurrence_id", "occurrence"),
            ("locator_id", "locator"),
            ("object_id", "object"),
            ("sha256", "sha256"),
            ("ref", "ref"),
        ):
            matched = [
                row for row in rows
                if str(row.get(field) or "") == identity
            ]
            if not matched:
                continue
            if matched_by in {"object", "sha256"}:
                object_ids = {
                    str(row.get("object_id") or "")
                    for row in matched
                }
                matched = [
                    row for row in rows
                    if str(row.get("object_id") or "") in object_ids
                ]
            return matched, matched_by
        return [], ""

    @staticmethod
    def _catalog_visibility(
        row: Mapping[str, Any],
        context: QueryContext,
    ) -> dict[str, Any]:
        item = dict(row)
        if artifact_access_allowed(
            item.get("access_scope"),
            actor=context.actor,
            role=context.role,
            purpose=context.purpose,
        ):
            item["authorized"] = True
            return item
        return {"authorized": False, "redacted": True}

    @staticmethod
    def _lineage_visibility(
        row: Mapping[str, Any],
        context: QueryContext,
    ) -> dict[str, Any]:
        if artifact_access_allowed(
            row.get("access_scope"),
            actor=context.actor,
            role=context.role,
            purpose=context.purpose,
        ):
            return {**dict(row), "authorized": True}
        return {"authorized": False, "redacted": True}


__all__ = ["ArtifactObjectViewMixin"]
