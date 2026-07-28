from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from zf.runtime.source_repair_restart_actuator import main


def test_restart_actuator_stops_old_watcher_then_execs_control_plane_only(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    state_dir = project / ".zf"
    state_dir.mkdir(parents=True)

    with patch(
        "zf.runtime.source_repair_restart_actuator._recorded_watcher_pid",
        return_value=1234,
    ), patch(
        "zf.runtime.source_repair_restart_actuator._terminate_watcher",
    ) as terminate, patch(
        "zf.runtime.source_repair_restart_actuator.zf_cli_cmd",
        return_value="uv run zf",
    ), patch(
        "zf.runtime.source_repair_restart_actuator.os.execvp",
        side_effect=RuntimeError("exec captured"),
    ) as execvp, patch(
        "zf.runtime.source_repair_restart_actuator.time.sleep",
    ), patch(
        "zf.runtime.source_repair_restart_actuator.os.chdir",
    ):
        with pytest.raises(RuntimeError, match="exec captured"):
            main([
                "--project-root",
                str(project),
                "--state-dir",
                str(state_dir),
            ])

    terminate.assert_called_once_with(1234)
    assert execvp.call_args.args == (
        "uv",
        ["uv", "run", "zf", "start", "--control-plane-only"],
    )
