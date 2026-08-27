from __future__ import annotations

import subprocess
import zlib
from pathlib import Path

from zf.runtime.self_issue_browser_evidence import (
    capture_self_issue_browser_evidence,
)


def _png() -> bytes:
    def chunk(kind: bytes, body: bytes) -> bytes:
        return (
            len(body).to_bytes(4, "big") + kind + body
            + (zlib.crc32(kind + body) & 0xFFFFFFFF).to_bytes(4, "big")
        )

    return b"\x89PNG\r\n\x1a\n" + b"".join((
        chunk(b"IHDR", (1).to_bytes(4, "big") * 2 + bytes((8, 2, 0, 0, 0))),
        chunk(b"IDAT", zlib.compress(b"\x00\x00\x00\x00")),
        chunk(b"IEND", b""),
    ))


def _context(base_url: str) -> dict:
    return {"browser_capture": {
        "requested": True,
        "target": "kanban_board",
        "base_url": base_url,
        "project_id": "demo project",
    }}


def test_browser_capture_rejects_non_loopback_before_starting_docker(tmp_path: Path) -> None:
    calls = []
    result = capture_self_issue_browser_evidence(
        state_dir=tmp_path,
        draft_id="sid-1",
        run_id="run-1",
        reporter_context=_context("https://example.com"),
        enabled=True,
        runner=lambda *args, **kwargs: calls.append((args, kwargs)),
    )
    assert result.status == "not_available"
    assert "loopback" in result.reason
    assert calls == []


def test_browser_capture_is_one_passive_docker_run_and_returns_local_ref(tmp_path: Path) -> None:
    observed: list[list[str]] = []
    scripts: list[str] = []

    def runner(command, **kwargs):
        observed.append(command)
        mount = next(command[index + 1] for index, value in enumerate(command) if value == "-v")
        host = Path(mount.split(":/evidence", 1)[0])
        scripts.append((host / "capture.js").read_text(encoding="utf-8"))
        (host / "playwright-clean-incident.png").write_bytes(_png())
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    result = capture_self_issue_browser_evidence(
        state_dir=tmp_path,
        draft_id="sid-1",
        run_id="run-1",
        reporter_context=_context("http://127.0.0.1:8002"),
        enabled=True,
        runner=runner,
    )

    assert result.status == "captured"
    assert result.screenshot_ref is not None
    assert result.screenshot_ref["capture_kind"] == "playwright_clean_reproduction"
    assert result.screenshot_ref["ref"].startswith("artifacts/self-issues/sid-1/browser/")
    assert len(observed) == 1
    command = observed[0]
    assert command[:3] == ["docker", "run", "--rm"]
    assert "--network" in command and "host" in command
    assert command[command.index("--user") + 1]
    assert "http://127.0.0.1:8002/?project=demo+project&page=board&view=board" in command
    assert not any("sh -c" in item for item in command)
    assert "executablePath: chromium.executablePath()" in scripts[0]


def test_browser_capture_failure_is_nonfatal_and_retains_no_image(tmp_path: Path) -> None:
    result = capture_self_issue_browser_evidence(
        state_dir=tmp_path,
        draft_id="sid-1",
        run_id="run-1",
        reporter_context=_context("http://localhost:8002"),
        enabled=True,
        runner=lambda command, **kwargs: subprocess.CompletedProcess(
            command, 78, stdout="", stderr="unsafe",
        ),
    )
    assert result.status == "unsafe_page"
    assert result.screenshot_ref is None
    assert not list(tmp_path.rglob("*.png"))
