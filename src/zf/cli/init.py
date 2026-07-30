"""zf init — initialize the configured runtime state directory."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from zf.core.config.loader import ConfigError
from zf.core.workspace.project_initializer import ProjectInitializer


def register(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser("init", help="Initialize .zf/ state directory")
    parser.add_argument("path", nargs="?", default=".",
                        help="Target project directory. Default: cwd")
    parser.add_argument("--create", action="store_true",
                        help="Create the target directory if it does not exist (from-0 new project)")
    parser.add_argument("--force", action="store_true", help="Re-initialize even if .zf/ exists")
    parser.add_argument(
        "--state-dir",
        type=str,
        default=None,
        help="Path to runtime state dir (default: project.state_dir from zf.yaml, else .zf)",
    )
    parser.add_argument("--preset", type=str, default=None,
                        help="Use a preset template (minimal, code-assist, design-first)")
    parser.add_argument(
        "--workspace",
        type=str,
        default="default",
        help="Workspace registry name for optional project registration",
    )
    parser.add_argument(
        "--workspace-register",
        action="store_true",
        help="Force registration into the workspace registry after init",
    )
    parser.add_argument(
        "--no-workspace-register",
        action="store_true",
        help="Initialize only this Project; do not register it in a workspace",
    )
    parser.add_argument(
        "--with-bootstrap",
        action="store_true",
        help="Auto-create the guided bootstrap feature with 4 starter tasks. "
             "Disabled by default so test fixtures and CI runs start with an "
             "empty state directory. Recommended for first-time interactive use.",
    )
    parser.add_argument(
        "--skip-instruction-docs",
        action="store_true",
        help="Do not create or refresh project AGENTS.md / CLAUDE.md during init",
    )
    parser.add_argument(
        "--notes",
        default="",
        help="Claude-specific operator notes / conventions appended to CLAUDE.md",
    )
    parser.add_argument(
        "--stack",
        default="",
        choices=["python", "node", "go", "rust"],
        help="Declare the project stack when it cannot be detected from repository files",
    )
    parser.add_argument(
        "--surface",
        default="",
        choices=["", "backend", "frontend", "fullstack", "library"],
        help="Optional surface override for a declared stack",
    )
    parser.add_argument(
        "--no-git-hooks",
        action="store_true",
        help="Do not install the ZaoFu pre-commit hook into .git/hooks",
    )
    parser.add_argument(
        "--env-check",
        action="store_true",
        help="Verify the hook command chain (zf hook-recv) resolves and runs; "
             "exit 1 on failure. `zf start` always enforces this preflight.",
    )
    parser.set_defaults(func=run)


def run(args: argparse.Namespace) -> int:
    project_root = Path(getattr(args, "path", ".") or ".").resolve()
    workspace_register = None
    if getattr(args, "no_workspace_register", False):
        workspace_register = False
    elif getattr(args, "workspace_register", False):
        workspace_register = True
    try:
        result = ProjectInitializer(
            workspace=str(getattr(args, "workspace", "default") or "default"),
        ).initialize(
            cwd=project_root,
            explicit_state_dir=getattr(args, "state_dir", None),
            force=bool(getattr(args, "force", False)),
            preset=getattr(args, "preset", None),
            with_bootstrap=bool(getattr(args, "with_bootstrap", False)),
            with_instruction_docs=not bool(
                getattr(args, "skip_instruction_docs", False)
            ),
            with_git_hooks=not bool(getattr(args, "no_git_hooks", False)),
            create_root=bool(getattr(args, "create", False)),
            workspace_register=workspace_register,
            notes=str(getattr(args, "notes", "") or ""),
            instruction_stack=str(getattr(args, "stack", "") or ""),
            instruction_surface=str(getattr(args, "surface", "") or ""),
        )
    except ConfigError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    except FileExistsError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    if getattr(args, "preset", None):
        print(f"Generated zf.yaml from preset: {args.preset}")
    print(f"Initialized {result.state_dir}")
    if result.registered_project is not None:
        print(
            "  + registered workspace project "
            f"{result.registered_project.project_id}"
        )
    if result.bootstrap_installed:
        print(
            f"  + F-zaofu-bootstrap installed (4 guided tasks). "
            f"Run `zf start` to begin, or read {result.state_dir}/bootstrap.md"
        )
    if result.instruction_docs.created:
        print(
            "  + instruction docs created: "
            f"{', '.join(result.instruction_docs.created)}"
        )
    if result.instruction_docs.updated:
        print(
            "  + instruction docs updated: "
            f"{', '.join(result.instruction_docs.updated)}"
        )
    if result.notes_applied:
        print(f"  + operator notes {result.notes_applied} -> CLAUDE.md")
    if result.feishu_channel_binding:
        print(
            "  + feishu channel binding "
            f"{result.feishu_channel_binding}: feishu.yaml"
        )
    if result.feishu_channel_bootstrap:
        print(
            "  + feishu default channel bootstrap "
            f"{result.feishu_channel_bootstrap}: zaofu"
        )
    if result.git_hook_status == "installed":
        print("  + git pre-commit hook installed (runtime truth guard and large-stage circuit breaker)")
    elif result.git_hook_status == "exists":
        print("  + git pre-commit hook already exists; left unchanged")
    if getattr(args, "env_check", False):
        from zf.runtime.cli_command import zf_cli_cmd
        from zf.runtime.env_preflight import check_hook_command

        check = check_hook_command(zf_cli_cmd())
        if not check.ok:
            print("Error: hook command-chain self-check failed; worker hooks will not run:", file=sys.stderr)
            print(f"  - {check.detail}", file=sys.stderr)
            print("  Repair the zf command resolution chain, then retry.",
                  file=sys.stderr)
            return 1
        print("  + hook command-chain self-check passed")
    else:
        print("  + note: `zf start` enforces environment preflight; use `zf init --env-check` to check hooks early")
    if result.setup_suggestion:
        print("  + project.scripts.setup is not declared, but a dependency manifest was detected; add this to zf.yaml:")
        print("      project:")
        print("        scripts:")
        print(f"          setup: {result.setup_suggestion}")
        print("    (runs when a worktree is created so a new worker worktree is ready to use)")
    _print_profile_hint(project_root)
    return 0


def _print_profile_hint(project_root: Path) -> None:
    """Post-init: detect stack + suggest a zf.yaml archetype (doc 102 §6)."""
    try:
        from zf.core.profile.detector import detect
        from zf.core.profile.recommender import recommend
    except Exception:
        return
    profile = detect(project_root)
    if profile.confidence == "low":
        print("  + detection: no recognizable stack yet; run `zf profile recommend` after code is added")
        return
    rec = recommend(profile, "build")
    langs = "+".join(profile.languages) or "unknown"
    print(f"  + detected stack: {langs}"
          f"{' (fullstack)' if profile.is_fullstack else ''} -> "
          f"recommended archetype={rec.archetype}, harness_profile={rec.harness_profile}")
    print("    Run `zf profile bootstrap --apply` to materialize it, or `zf profile recommend` for details")
