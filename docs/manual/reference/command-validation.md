# 文档命令验证

[English](command-validation.en.md) · [参考索引](README.md)

文档命令验证分两层，不能用其中一层替代另一层：

1. `scripts/manual_commands.py` 扫描 `docs/manual` 的 shell fence，按当前 argparse tree
   校验 `zf` 命令路径和参数名；
2. `scripts/manual-command-smoke.py` 按
   [`command-validation-matrix.yaml`](command-validation-matrix.yaml) 在有终态证据的项目上
   实际执行代表性命令，记录退出码、耗时、副作用和 state 前后守卫。

`scripts/manual-docs.py check` 已包含第一层和 matrix schema 检查。第二层依赖本机已有项目，
不会在普通 CI 中猜测路径或调用真实 Provider。

## 副作用分类

| Class | 可以直接用于完成项目 | 允许的 state 变化 |
|---|---|---|
| `completed-project-readonly` | 是 | 无 |
| `completed-project-runtime-cache-refresh` | 是 | 已声明的 config validation cache |
| `completed-project-projection-refresh` | 是 | 仅 `projections/` |
| `completed-project-projection-output` | 是 | 仅 `projections/`，并写显式输出目录 |
| `snapshot-*` | 仅快照 | matrix 声明的 projection 或 canonical event |
| `live-runtime-*` / `external-*` | 否 | 需要 Worker credential、Provider、Web 或外部集成 |

“只读 projection”表示不拥有 canonical Task/Run/Event authority，不保证 state dir 字节级零
写入。Smoke 会单独守卫 `events.jsonl`、Kanban、Feature、Session 和 refs 等 canonical 文件。

## 运行 Manifest

项目路径和真实 ID 不提交进 matrix。为一次本机验证创建临时 manifest：

```yaml
schema_version: zf-manual-command-smoke-run.v1
project:
  name: completed-project
  root: /absolute/project/root
  config_path: /absolute/project/root/zf.yaml
  state_dir: /absolute/project/root/.zf
  state_kind: completed-original
  terminal_evidence:
    - event_type: run.goal.completed
      task_id: GOAL-TASK-ID
      run_id: RUN-ID
context:
  task_id: TASK-ID
  run_id: RUN-ID
cases:
  - status-workers
  - task-trace
  - events-tail
  - projection-status
```

执行并把回执写到 state dir 之外：

```bash
uv run python scripts/manual-command-smoke.py \
  --manifest /tmp/zf-manual-command-smoke.yaml \
  --receipt-dir /tmp/zf-manual-command-smoke-receipt
```

回执包含终态 event id、每条命令的退出码/耗时、stdout/stderr digest、变化文件和 canonical
hash。会写 canonical event 的 case 必须使用复制到 `/tmp` 的快照；需要 attempt-scoped
credential 的正向 `artifact read` 只能在真实 Worker 派遣上下文验证。
