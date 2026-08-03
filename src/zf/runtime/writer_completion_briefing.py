"""Render writer success and blocked-checkpoint completion discipline."""

from __future__ import annotations


def writer_completion_discipline_lines(
    *,
    completion_command: str,
    blocked_command: str,
) -> list[str]:
    return [
        "## Completion discipline (candidate integration depends on it)",
        "1. COMMIT only this task's `files_touched` before emitting dev.build.done. "
        "Stage them with explicit pathspecs (`git add -- <path>...`); never use "
        "`git add -A`, `git add .`, or `git commit -a`. Materialized runtime files "
        "such as `.claude/` and `.zf-setup.done` are not task output. An uncommitted "
        "task file is rejected at integration (\"workdir has uncommitted\").",
        "2. The `source_commit` you report MUST be the current branch HEAD. Do NOT "
        "touch files or commit again after emitting dev.build.done; a later commit "
        "makes the reported source_commit stale (\"source_commit is not HEAD\") and "
        "the ref is rejected.",
        "3. Stay strictly inside `allowed_paths`. Do NOT create or edit files another "
        "slice owns; overlapping a sibling's paths is rejected (\"changes outside "
        "contract scope\") and conflicts at cherry-pick integration.",
        "4. Identity fields (`fanout_id`/`run_id`/`child_id`) are kernel audit fields, "
        "pre-filled by this command; you never need to manage or update them. If you "
        "re-emit after a re-dispatch, the kernel may adopt it only when the canonical "
        "contract revision and task-map generation are unchanged. A stale contract "
        "is superseded and cannot advance the current child.",
        "5. Fill `impl_self_check` after running each declared command. Replace every "
        "placeholder with the current HEAD, exact command receipt evidence, and one "
        "result for every mandatory AC. Do not claim that a command or AC passed "
        "without a durable artifact/event ref.",
        "6. If the contract is blocked after producing useful task-owned changes, "
        "preserve them before `dev.blocked`: first remove generated runtime/cache "
        "files, verify every remaining dirty path is inside `allowed_paths`, stage "
        "only those paths with explicit pathspecs, and create a checkpoint commit. "
        "Record which focused checks passed and which mandatory gate blocked. This "
        "checkpoint is continuation evidence only; it is not `dev.build.done` and "
        "must not be described as candidate-ready.",
        "7. After the blocked checkpoint commit, do not edit or commit again before "
        "submitting `dev.blocked`. The kernel captures that exact HEAD, branch, and "
        "cleanliness for the semantic-replan continuation. If no useful task-owned "
        "change exists, submit the blocker without creating an empty commit.",
        "",
        "When finished, update `<HEAD commit>` and `files_touched`, then emit "
        "dev.build.done with the runtime state dir explicitly:",
        "```bash",
        completion_command,
        "```",
        "",
        "If the contract cannot be satisfied inside `allowed_paths`, do not search "
        "runtime source or emit success. Fill the blocker evidence in the signed "
        "result scratch, then submit:",
        "```bash",
        blocked_command,
        "```",
        "",
    ]


__all__ = ["writer_completion_discipline_lines"]
