# 认领：rework-loop

<!-- 本文件由 Qoder 创建。其他 AI 请勿修改本文件；改你自己的即可，永不冲突。 -->

| 字段 | 值 |
|---|---|
| task_id | `rework-loop` |
| Harness | Qoder |
| 认领时间 | 2026-10-08 12:16 |
| 分支 | `agent/local/qoder/rework-loop/a1` |
| 状态 | ✅ done（实现 af80d4e；待合入，外部验收待认） |

任务表对应行：`TASKS.md` → 「任务表」章节，搜 `rework-loop`。

## 变更记录

- 2026-10-08 12:16：认领（协调者「开 F1 卡」）。来自 e2e-harness-drill 报告
  F1（高）：`changes_requested → claimed` 的 `claim_for_revision` 与
  `resubmit` 只有事件常量（spec.py L126/128）与冻结迁移表边，**无实现**——
  verify 失败后重领被 `claim.already_owned` 永久挡住（current_attempt_id
  不清），返工是死端。本卡实现返工闭环（claim_for_revision 路径 +
  list_ready_tasks 可发现性）。

- 2026-10-08 12:52：完成。实现 `af80d4e`；全量门禁绿（pytest 451 passed，净增
  8；docstate 90/0、discipline 44/0、ruff 119 files）。变异验证（注掉分支必红）。
  实机复核：演练实例 T-CDX seq97 吃到 claim_for_revision（revised_from=
  T-CDX.a1、a2/epoch2）——F1 死端消失（后段被 relay 断流中断，如实记录）。
  两处 R10 语义裁决（base 重冻于 integration head；resubmit 字面边不落实现
  ——会绕 §16 receipt）见卡内范围声明。
