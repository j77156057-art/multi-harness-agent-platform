# 认领：runtime-driver

<!-- 本文件由 Qoder 创建。其他 AI 请勿修改本文件；改你自己的即可，永不冲突。 -->

| 字段 | 值 |
|---|---|
| task_id | `runtime-driver` |
| Harness | Qoder |
| 认领时间 | 2026-10-08 13:05 |
| 分支 | `agent/local/qoder/runtime-driver/a1` |
| 状态 | 🚧 doing |

任务表对应行：`TASKS.md` → 「任务表」章节，搜 `runtime-driver`。

## 变更记录

- 2026-10-08 13:05：认领（协调者路线图第 1 项 / drill F6）。把 VerifyWorker 与
  MergeWorker 从"测试与操作面脚本驱动"接入正式运行环：新 `app/runtime`
  （scan + 有界执行）+ `python -m app.runtime [--once|--check|--loop]`。
  Review 侧如实裁决：无 ReviewWorker 类也不需要——审查是**外部 harness 主体
  行为**（API claim/submit），平台侧只有队列、SLA 与 §16 串行门禁（已有）。
