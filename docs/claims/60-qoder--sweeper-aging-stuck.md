# 认领：sweeper-aging-stuck

<!-- 本文件由 Qoder 创建。其他 AI 请勿修改本文件；改你自己的即可，永不冲突。 -->

| 字段 | 值 |
|---|---|
| task_id | `sweeper-aging-stuck` |
| Harness | Qoder |
| 认领时间 | 2026-10-07 04:30 |
| 分支 | `agent/local/qoder/sweeper-aging-stuck/a1` |
| 状态 | 🟢 doing |

任务表对应行：`TASKS.md` → 「任务表」章节，搜 `sweeper-aging-stuck`。

## 变更记录

- 2026-10-07 04:30：认领。前置：`sweeper-runner`（`fbc4496`）与
  `sweeper-deps-resources`（`696eea8`）已合入；设计 §6 第 3 项，覆盖 §2.3/§2.4/§2.5
  （老化、阻塞 SLA、卡住指纹）。`sweep_progress` 表已在 007 备好。
- 附带修一条链路：`progress_hash` 目前**没有生产者**——schema 缺字段、API 静默
  丢弃、列上无写入。卡住指纹需要它，本任务把 heartbeat → tasks.progress_hash 接通。
