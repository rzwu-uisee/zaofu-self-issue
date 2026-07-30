"""Mechanical safety checks for executable Task Contract commands."""

from __future__ import annotations

import re


_INLINE_EVAL_RE = re.compile(
    r"(?<![\w./-])(?:node|python(?:3(?:\.\d+)*)?)\s+"
    r"(?:-e|-c|--eval(?:=|\s+))"
)


def escape_sensitive_inline_eval_errors(command: str) -> list[str]:
    """Reject inline programs whose backslashes cross interpreter layers.

    A raw ``\\`` after ``node -e``/``python -c`` is interpreted once by the
    shell and again by the embedded language. The command can remain valid
    shell syntax while changing the program. Script or test files preserve the
    command identity consumed by candidate verification.
    """

    match = _INLINE_EVAL_RE.search(command)
    if match is None or "\\\\" not in command[match.end():]:
        return []
    return [
        "must not embed escape-sensitive code in node -e/python -c; "
        "move the assertion into a repository test/script file or use a "
        "command without nested backslash escaping"
    ]


__all__ = ["escape_sensitive_inline_eval_errors"]
