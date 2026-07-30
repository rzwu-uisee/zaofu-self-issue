from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from zf.integrations.feishu.mock_clients import MockFeishuBitableClient
from zf.integrations.feishu.projection_target import (
    FeishuKanbanTargetStore,
    redact_target_url,
    resolve_or_create_kanban_target,
)


def test_target_bootstrap_is_single_writer(tmp_path: Path):
    class SlowClient(MockFeishuBitableClient):
        def create_base(self, **kwargs):
            time.sleep(0.05)
            return super().create_base(**kwargs)

    client = SlowClient()

    def resolve():
        return resolve_or_create_kanban_target(
            state_dir=tmp_path / ".zf",
            project_name="project-a",
            client=client,
            create_if_missing=True,
            folder_token="fld-project",
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: resolve(), range(2)))

    assert len(client.created_bases) == 1
    assert len(client.created_tables) == 1
    assert sorted(result.created for result in results) == [False, True]


def test_target_bootstrap_resumes_incomplete_shape(tmp_path: Path):
    class FailOnceClient(MockFeishuBitableClient):
        failed = False

        def ensure_fields(self, app_token, table_id, field_specs):
            if not self.failed:
                self.failed = True
                raise RuntimeError("temporary schema failure")
            return super().ensure_fields(app_token, table_id, field_specs)

    client = FailOnceClient()
    kwargs = {
        "state_dir": tmp_path / ".zf",
        "project_name": "project-a",
        "client": client,
        "create_if_missing": True,
        "folder_token": "fld-project",
    }

    with pytest.raises(RuntimeError, match="temporary schema failure"):
        resolve_or_create_kanban_target(**kwargs)

    incomplete = FeishuKanbanTargetStore.for_state_dir(tmp_path / ".zf").read()
    assert incomplete is not None
    assert incomplete.ready is False

    result = resolve_or_create_kanban_target(**kwargs)

    assert result.created is True
    assert result.target.ready is True
    assert len(client.created_bases) == 1
    assert len(client.created_tables) == 1


def test_target_bootstrap_requires_explicit_opt_in(tmp_path: Path):
    with pytest.raises(ValueError, match="APP_TOKEN"):
        resolve_or_create_kanban_target(
            state_dir=tmp_path / ".zf",
            project_name="project-a",
            client=MockFeishuBitableClient(),
        )


def test_target_url_redacts_embedded_base_token():
    assert redact_target_url(
        "https://example.feishu.cn/base/app-sensitive-token",
        "app-sensitive-token",
    ) == "https://example.feishu.cn/base/app-...oken"
