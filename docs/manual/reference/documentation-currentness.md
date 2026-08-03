# 文档 Currentness 与发布门禁

[English](documentation-currentness.en.md) · [参考索引](README.md)

## 目标

用户手册不能依赖维护者记得同步。当前门禁把四类易漂移事实变成可执行检查：

1. CLI 命令目录直接来自 `zf.cli.main.build_parser()`；
2. 发布面能力通过 `capability-coverage.yaml` 绑定 manual/code/test；
3. capability release note 明确启用、回读、回退、权限边界和用户文档。
4. shell fence 中的 CLI 路径/参数来自当前 parser，代表性命令另有完成项目实跑 matrix。

## 修改协议

当一个改动新增、移除或改变用户可见能力时：

1. 先更新 canonical 中英文用户手册；
2. 更新 `capability-coverage.yaml` 的状态和证据路径；
3. CLI parser 有变化时只改 parser/help，不手工编辑生成目录；
4. 重新生成并执行检查；
5. release note 宣布该能力时，执行 release smoke。

```bash
uv run python scripts/manual-docs.py generate
uv run python scripts/manual-docs.py check
```

`check` 会验证：

- 四份生成文档与 parser/YAML 一致；
- coverage ID、状态、双语手册、代码、测试和 release 元数据完整；
- coverage 手册可从对应语言的 `00-index` 路由到达；
- `docs/manual` 本地 Markdown 链接存在；
- `docs/manual` 不依赖 `docs/design`，用户路径保持自包含；
- 已裁决的 Channel/Layer 2 旧全局叙事没有重新进入当前手册。
- shell fence 中的 `zf` 命令路径和 option 存在，执行 matrix 的来源文档可达。

`check` 证明命令契约存在，不代表已经调用 Provider 或在真实项目执行。完成项目实跑见
[文档命令验证](command-validation.md)。

## Capability Coverage 的边界

清单覆盖**发布面产品能力**，不是每个 Python module。条目只有在已有真实 caller 和回归证据时
才能标记 `implemented`；只有相邻 library 或 mock fixture 时必须标记 `partial` 或
`candidate`。Code/test 证明当前实现，manual 说明用户如何激活和回读。

## Release Smoke

在 release note 中为本次宣布的 capability 添加模板块：

```markdown
<!-- ZF-CAPABILITY: controlled-workflow-start -->
- Activation / 启用: 用户如何启用或进入能力。
- Readback / 回读: 用户从哪里确认能力生效。
- Rollback / 回退: 如何停用、撤销或恢复，不破坏 canonical history。
- Authority / 权限边界: 谁能决定、谁能写状态、哪些动作必须批准。
- Manual / 文档: [受控 Workflow 启动](../workflows/controlled-workflow-start.md)
<!-- ZF-CAPABILITY-END -->
```

链接路径必须相对实际 release note 所在目录调整。然后只声明本次用户可见 surface：

```bash
uv run python scripts/manual-docs.py release-check \
  --release-notes docs/releases/NEXT.md \
  --surface controlled-workflow-start
```

多个 capability 重复 `--surface`。该 gate 不证明真实 Provider E2E；它证明发布叙事没有遗漏
可操作合同。行为验证仍按 unit/integration/scripted/real-provider/Web 分层执行。

## 生成文件

以下文件禁止手工修改：

- `cli-command-index.md` / `.en.md`；
- `capability-coverage.md` / `.en.md`。

唯一来源分别是 argparse parser 和 `capability-coverage.yaml`。
