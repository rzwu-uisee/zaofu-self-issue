"""Environment boundary for Web terminal helper and provider processes."""

from __future__ import annotations

import os


_CONTROL_PLANE_SECRET_MARKERS = ("TOKEN", "PASSCODE", "SECRET", "ENCRYPT_KEY")
_PARENT_AGENT_CONTEXT_KEYS = frozenset(
    {
        "CLAUDECODE",
        "CLAUDE_CODE_ENTRYPOINT",
        "CLAUDE_CODE_SESSION_ID",
        "CODEX_CI",
        "CODEX_SESSION_ID",
        "CODEX_THREAD_ID",
    }
)


def terminal_subprocess_env() -> dict[str, str]:
    """Copy the host environment without ZaoFu control-plane credentials."""

    env = dict(os.environ)
    for key in tuple(env):
        if key in _PARENT_AGENT_CONTEXT_KEYS or (
            key.startswith("ZF_")
            and any(marker in key for marker in _CONTROL_PLANE_SECRET_MARKERS)
        ):
            env.pop(key, None)
    return env
