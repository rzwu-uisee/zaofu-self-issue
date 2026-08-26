# ZaoFu Public Export Manifest

- Source ref: `742d660408052afe9140d32a45c9ca489e351d3a`
- Generated UTC: `2026-08-26T13:08:14Z`

## Included

- `AGENTS.md`
- `CLAUDE.md`
- `zf.yaml`
- `feishu.yaml`
- `README.md`
- `README.zh-CN.md`
- `CHANGELOG.md`
- `LICENSE`
- `DISCLAIMER.md`
- `assets/readme`
- `.python-version`
- `.env.example`
- `pyproject.toml`
- `uv.lock`
- `src`
- `web`
- `examples`
- `tests`
- `tools`
- `scripts`
- `skills`
- `yoke`
- `channel_roles`
- `.claude/rules`
- `.claude/commands/audit-backlogs.md`
- `.claude/skills`
- `.codex/skills`
- `docs/manual`
- `docs/releases`

## Explicitly Excluded

- git history and private branches
- `.claude/` local settings and worktrees; reviewed rules, the maintenance
  command, and tracked skill mirrors are included
- `.codex/` local state except tracked skill mirrors; `.zf/`, runtime
  state, caches, and local env files
- all `docs/` subtrees except `docs/manual/` and `docs/releases/`
- `backlogs/`, `tasks/`, `prompt/`, `prompts/`, `ideas/`, `reports/`, `slides/`
- project-specific `skills/cangjie-*`

## Required Manual Checks Before Publishing

- Confirm `LICENSE` and `DISCLAIMER.md` owner/copyright attribution before publishing.
- Review README links after public-only docs filtering.
- Run secret scanning before pushing to a public remote.
