from __future__ import annotations

from pathlib import Path

from zf.core.config.schema import (
    ProjectConfig,
    RuntimeConfig,
    RuntimeFeishuProjectionConfig,
    ZfConfig,
)
from zf.integrations.feishu.mock_clients import MockFeishuBitableClient
from zf.runtime.feishu_projection_sidecar import (
    build_feishu_projection_command,
    start_feishu_projection_sidecar,
    stop_feishu_projection_sidecar,
    stop_feishu_projection_sidecar_by_pidfile,
)


class EventSink:
    def __init__(self) -> None:
        self.events = []

    def append(self, event) -> None:
        self.events.append(event)


class FakeProcess:
    pid = 5151
    returncode = None
    terminated = False

    def poll(self):
        return self.returncode

    def terminate(self):
        self.terminated = True
        self.returncode = 0

    def wait(self, timeout=None):
        return self.returncode

    def kill(self):
        self.returncode = -9


def test_projection_sidecar_command_uses_current_zf_cli(monkeypatch):
    monkeypatch.setenv("ZF_CLI_CMD", "uv --project /repo run zf")

    command = build_feishu_projection_command(
        state_dir=Path("/tmp/state"),
        poll_interval_seconds=2.5,
    )

    assert command[:8] == [
        "uv",
        "--project",
        "/repo",
        "run",
        "zf",
        "feishu",
        "project-kanban",
        "--watch",
    ]
    assert command[-2:] == ["--poll-interval-seconds", "2.5"]


def test_projection_sidecar_starts_and_stops(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("ZF_CLI_CMD", "zf")
    monkeypatch.setenv("FEISHU_APP_ID", "cli_test")
    monkeypatch.setenv("FEISHU_APP_SECRET", "secret")
    monkeypatch.setenv("FEISHU_BITABLE_APP_TOKEN", "app")
    monkeypatch.setenv("FEISHU_BITABLE_TABLE_ID", "tbl")
    started = {}
    process = FakeProcess()

    def fake_start(command, **kwargs):
        started["command"] = command
        started["cwd"] = kwargs["cwd"]
        return process

    monkeypatch.setattr(
        "zf.runtime.feishu_projection_sidecar._start_projection_process",
        fake_start,
    )
    config = ZfConfig(
        runtime=RuntimeConfig(
            feishu_projection=RuntimeFeishuProjectionConfig(
                enabled=True,
                backend="lark-cli",
                poll_interval_seconds=3.0,
            )
        )
    )
    events = EventSink()

    sidecar = start_feishu_projection_sidecar(
        config=config,
        state_dir=tmp_path / ".zf",
        project_root=tmp_path,
        event_log=events,
    )

    assert sidecar is not None
    assert started["command"][:4] == ["zf", "feishu", "project-kanban", "--watch"]
    assert started["cwd"] == str(tmp_path)
    assert sidecar.pid_path.exists()

    stop_feishu_projection_sidecar(sidecar, event_log=events)

    assert process.terminated is True
    assert not sidecar.pid_path.exists()
    assert events.events[-1].type == "feishu.kanban_projection.stopped"


def test_projection_sidecar_auto_creates_project_target(
    tmp_path: Path,
    monkeypatch,
):
    monkeypatch.setenv("ZF_CLI_CMD", "zf")
    monkeypatch.setenv("FEISHU_APP_ID", "cli_test")
    monkeypatch.setenv("FEISHU_APP_SECRET", "secret")
    monkeypatch.setenv("FEISHU_FOLDER_TOKEN", "fld-project")
    monkeypatch.delenv("FEISHU_BITABLE_APP_TOKEN", raising=False)
    monkeypatch.delenv("FEISHU_BITABLE_TABLE_ID", raising=False)
    client = MockFeishuBitableClient()
    monkeypatch.setattr(
        "zf.runtime.feishu_projection_sidecar.LarkCliBitableClient",
        lambda: client,
    )
    process = FakeProcess()
    started = {}

    def fake_start(command, **kwargs):
        started["command"] = command
        return process

    monkeypatch.setattr(
        "zf.runtime.feishu_projection_sidecar._start_projection_process",
        fake_start,
    )
    config = ZfConfig(
        project=ProjectConfig(name="project-a"),
        runtime=RuntimeConfig(
            feishu_projection=RuntimeFeishuProjectionConfig(
                enabled=True,
                auto_create_target=True,
            )
        )
    )
    events = EventSink()

    sidecar = start_feishu_projection_sidecar(
        config=config,
        state_dir=tmp_path / ".zf",
        project_root=tmp_path,
        event_log=events,
    )

    assert sidecar is not None
    assert client.created_bases[0]["name"] == "ZaoFu Kanban - project-a"
    assert client.created_bases[0]["folder_token"] == "fld-project"
    assert "--app-token" in started["command"]
    assert "--table-id" in started["command"]
    assert (
        tmp_path
        / ".zf"
        / "integrations"
        / "feishu"
        / "kanban-target.json"
    ).exists()
    assert any(
        event.type == "feishu.kanban_projection.target_created"
        for event in events.events
    )
    created_event = next(
        event
        for event in events.events
        if event.type == "feishu.kanban_projection.target_created"
    )
    assert created_event.payload["base_url"].endswith("/app-...ck-1")

    stop_feishu_projection_sidecar(sidecar)


def test_projection_sidecar_project_target_beats_inherited_environment(
    tmp_path: Path,
    monkeypatch,
):
    monkeypatch.setenv("ZF_CLI_CMD", "zf")
    monkeypatch.setenv("FEISHU_APP_ID", "cli_test")
    monkeypatch.setenv("FEISHU_APP_SECRET", "secret")
    monkeypatch.setenv("FEISHU_BITABLE_APP_TOKEN", "app-global")
    monkeypatch.setenv("FEISHU_BITABLE_TABLE_ID", "tbl-global")
    target_dir = tmp_path / ".zf" / "integrations" / "feishu"
    target_dir.mkdir(parents=True)
    (target_dir / "kanban-target.json").write_text(
        """{
  "schema_version": "feishu-kanban-target.v1",
  "app_token": "app-project",
  "table_id": "tbl-project",
  "base_url": "",
  "ready": true
}
""",
        encoding="utf-8",
    )
    process = FakeProcess()
    started = {}

    def fake_start(command, **kwargs):
        started["command"] = command
        return process

    monkeypatch.setattr(
        "zf.runtime.feishu_projection_sidecar._start_projection_process",
        fake_start,
    )
    config = ZfConfig(
        runtime=RuntimeConfig(
            feishu_projection=RuntimeFeishuProjectionConfig(enabled=True)
        )
    )

    sidecar = start_feishu_projection_sidecar(
        config=config,
        state_dir=tmp_path / ".zf",
        project_root=tmp_path,
        event_log=EventSink(),
    )

    assert sidecar is not None
    assert started["command"][
        started["command"].index("--app-token") + 1
    ] == "app-project"
    assert started["command"][
        started["command"].index("--table-id") + 1
    ] == "tbl-project"
    stop_feishu_projection_sidecar(sidecar)


def test_projection_sidecar_auto_create_fails_without_folder_token(
    tmp_path: Path,
    monkeypatch,
):
    monkeypatch.setenv("FEISHU_APP_ID", "cli_test")
    monkeypatch.setenv("FEISHU_APP_SECRET", "secret")
    monkeypatch.delenv("FEISHU_FOLDER_TOKEN", raising=False)
    monkeypatch.delenv("FEISHU_BITABLE_APP_TOKEN", raising=False)
    monkeypatch.delenv("FEISHU_BITABLE_TABLE_ID", raising=False)
    events = EventSink()
    config = ZfConfig(
        runtime=RuntimeConfig(
            feishu_projection=RuntimeFeishuProjectionConfig(
                enabled=True,
                auto_create_target=True,
            )
        )
    )

    sidecar = start_feishu_projection_sidecar(
        config=config,
        state_dir=tmp_path / ".zf",
        project_root=tmp_path,
        event_log=events,
    )

    assert sidecar is None
    assert events.events[-1].type == "feishu.kanban_projection.failed"
    assert events.events[-1].payload["reason"] == "target_bootstrap_failed"


def test_projection_sidecar_fails_closed_without_credentials(tmp_path: Path):
    config = ZfConfig(
        runtime=RuntimeConfig(
            feishu_projection=RuntimeFeishuProjectionConfig(enabled=True)
        )
    )
    events = EventSink()

    sidecar = start_feishu_projection_sidecar(
        config=config,
        state_dir=tmp_path / ".zf",
        project_root=tmp_path,
        event_log=events,
    )

    assert sidecar is None
    assert events.events[-1].type == "feishu.kanban_projection.failed"
    assert events.events[-1].payload["reason"] == "missing_target_or_credentials"


def test_projection_sidecar_pidfile_stop_validates_process_identity(
    tmp_path: Path,
    monkeypatch,
):
    state_dir = tmp_path / ".zf"
    pid_path = state_dir / "processes" / "feishu-kanban-projector.pid.json"
    pid_path.parent.mkdir(parents=True)
    pid_path.write_text(
        '{"pid": 6161, "backend": "lark-cli", "log_path": "/tmp/projector.log"}',
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "zf.runtime.feishu_projection_sidecar._pid_is_projector",
        lambda pid: pid == 6161,
    )
    stopped = []
    monkeypatch.setattr(
        "zf.runtime.feishu_projection_sidecar._terminate_process_group",
        lambda pid, timeout: stopped.append((pid, timeout)) or True,
    )
    events = EventSink()

    result = stop_feishu_projection_sidecar_by_pidfile(
        state_dir,
        event_log=events,
        timeout=2.0,
    )

    assert result is True
    assert stopped == [(6161, 2.0)]
    assert not pid_path.exists()
    assert events.events[-1].payload["stopped_by"] == "pidfile"


def test_projection_sidecar_pidfile_stop_does_not_signal_reused_pid(
    tmp_path: Path,
    monkeypatch,
):
    state_dir = tmp_path / ".zf"
    pid_path = state_dir / "processes" / "feishu-kanban-projector.pid.json"
    pid_path.parent.mkdir(parents=True)
    pid_path.write_text('{"pid": 7171}', encoding="utf-8")
    monkeypatch.setattr(
        "zf.runtime.feishu_projection_sidecar._pid_is_projector",
        lambda pid: False,
    )

    assert stop_feishu_projection_sidecar_by_pidfile(state_dir) is False
    assert not pid_path.exists()


def test_projection_sidecar_pidfile_stop_preserves_live_owner_on_signal_failure(
    tmp_path: Path,
    monkeypatch,
):
    state_dir = tmp_path / ".zf"
    pid_path = state_dir / "processes" / "feishu-kanban-projector.pid.json"
    pid_path.parent.mkdir(parents=True)
    pid_path.write_text('{"pid": 8181}', encoding="utf-8")
    monkeypatch.setattr(
        "zf.runtime.feishu_projection_sidecar._pid_is_projector",
        lambda pid: True,
    )
    monkeypatch.setattr(
        "zf.runtime.feishu_projection_sidecar._terminate_process_group",
        lambda pid, timeout: False,
    )
    monkeypatch.setattr(
        "zf.runtime.feishu_projection_sidecar._pid_is_alive",
        lambda pid: True,
    )

    assert stop_feishu_projection_sidecar_by_pidfile(state_dir) is False
    assert pid_path.exists()
