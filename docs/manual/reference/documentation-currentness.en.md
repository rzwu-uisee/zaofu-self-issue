# Documentation Currentness and Release Gate

[中文](documentation-currentness.md) · [Reference index](README.en.md)

## Goal

Manual currentness must not depend on a maintainer remembering to synchronize prose. The gate makes four
drift-prone contracts executable:

1. CLI inventory comes directly from `zf.cli.main.build_parser()`;
2. release-facing capabilities bind manual/code/test in `capability-coverage.yaml`;
3. capability release notes state activation, readback, rollback, authority, and user documentation.
4. CLI paths/options in shell fences track the current parser, with representative commands assigned to a completed-project execution matrix.

## Change Protocol

When a change adds, removes, or alters a user-visible capability:

1. update the canonical Chinese and English user guides;
2. update status and evidence paths in `capability-coverage.yaml`;
3. change parser/help for CLI changes, never the generated inventory;
4. regenerate and run currentness checks;
5. run release smoke when announcing the capability.

```bash
uv run python scripts/manual-docs.py generate
uv run python scripts/manual-docs.py check
```

`check` verifies:

- four generated documents match parser/YAML sources;
- coverage IDs, status, bilingual manuals, code, tests, and release metadata are complete;
- each coverage manual is reachable from its language's `00-index`;
- local Markdown links under `docs/manual` exist;
- `docs/manual` does not depend on `docs/design`, keeping user routes self-contained;
- resolved global Channel/Layer 2 narratives do not re-enter current manuals.
- `zf` paths and options in shell fences exist and execution-matrix source documents are present.

`check` proves command contracts exist; it does not invoke a provider or execute
against a real project. See [documentation command validation](command-validation.en.md)
for completed-project smoke.

## Capability Coverage Boundary

The catalog covers **release-facing product capabilities**, not every Python module. Mark an entry
`implemented` only with a real caller and regression evidence. A neighboring library or mock fixture alone
requires `partial` or `candidate`. Code/tests prove current implementation, and manuals explain user activation
and readback.

## Release Smoke

Add a block for each announced capability:

```markdown
<!-- ZF-CAPABILITY: controlled-workflow-start -->
- Activation / 启用: How a user enables or enters the capability.
- Readback / 回读: Where the user verifies that it took effect.
- Rollback / 回退: How to disable, undo, or recover without destroying canonical history.
- Authority / 权限边界: Who decides, who writes state, and which effects require approval.
- Manual / 文档: [Controlled Workflow Start](../workflows/controlled-workflow-start.en.md)
<!-- ZF-CAPABILITY-END -->
```

Adjust the link relative to the actual release-note directory. Validate only surfaces announced by the release:

```bash
uv run python scripts/manual-docs.py release-check \
  --release-notes docs/releases/NEXT.en.md \
  --surface controlled-workflow-start
```

Repeat `--surface` for multiple capabilities. This gate does not prove real-provider E2E. It proves that
the release story includes an operable contract; behavior still follows unit/integration/scripted/
real-provider/Web validation tiers.

## Generated Files

Do not edit these files manually:

- `cli-command-index.md` / `.en.md`;
- `capability-coverage.md` / `.en.md`.

Their only sources are the argparse parser and `capability-coverage.yaml`.
