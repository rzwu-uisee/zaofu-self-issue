# Capability Release Smoke Template

> 复制需要宣布的 block 到实际 release note，并把 capability ID 替换为
> `capability-coverage.yaml` 中已登记的 ID。本文只作模板，不是一次发布声明。

<!-- ZF-CAPABILITY: controlled-workflow-start -->
- Activation / 启用: 从真实 Task 查询 active route，preview/propose 后由 operator apply exact proposal。
- Readback / 回读: 在 Workflows/Task Trace 中确认同一 Task、route、parameters 和 proposal event 绑定到 `workflow.invoke.requested`。
- Rollback / 回退: 未 apply 的 proposal 直接 reject/supersede；已 admission 的 Run 使用受控 cancel/recovery action。
- Authority / 权限边界: route/Plan 选择不是审批；只有 operator controlled action 可应用，Provider Agent 不得获得 action token。
- Manual / 文档: [受控 Workflow 启动](../workflows/controlled-workflow-start.md)
<!-- ZF-CAPABILITY-END -->

验证示例：

```bash
uv run python scripts/manual-docs.py release-check \
  --release-notes docs/manual/reference/release-capability-template.md \
  --surface controlled-workflow-start
```
