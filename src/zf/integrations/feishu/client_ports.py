"""Ports implemented by Feishu projection backends."""

from __future__ import annotations

from typing import Any, Protocol


class DocumentClient(Protocol):
    def create_document(
        self,
        *,
        title: str,
        folder_token: str = "",
        content: str = "",
    ) -> dict[str, Any]: ...

    def append_markdown(self, document_id: str, markdown: str) -> dict[str, Any]: ...


class BitableClient(Protocol):
    def create_base(
        self,
        *,
        name: str,
        folder_token: str = "",
        time_zone: str = "",
    ) -> dict[str, Any]: ...

    def create_table(self, app_token: str, *, name: str) -> dict[str, Any]: ...

    def ensure_fields(
        self,
        app_token: str,
        table_id: str,
        field_specs: list[dict[str, object]],
    ) -> dict[str, Any]: ...

    def ensure_views(
        self,
        app_token: str,
        table_id: str,
        view_specs: list[dict[str, str]],
    ) -> dict[str, Any]: ...

    def ensure_view_layouts(
        self,
        app_token: str,
        table_id: str,
        layout_specs: list[dict[str, object]],
    ) -> dict[str, Any]: ...

    def find_record_id(
        self,
        app_token: str,
        table_id: str,
        *,
        key_field: str,
        key_value: str,
    ) -> str: ...

    def create_record(
        self,
        app_token: str,
        table_id: str,
        fields: dict[str, Any],
    ) -> str: ...

    def update_record(
        self,
        app_token: str,
        table_id: str,
        record_id: str,
        fields: dict[str, Any],
    ) -> str: ...
