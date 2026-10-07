# 认领：liveness-watch

<!-- 本文件由 Qoder 创建。其他 AI 请勿修改本文件；改你自己的即可，永不冲突。 -->

| 字段 | 值 |
|---|---|
| task_id | `liveness-watch` |
| Harness | Qoder |
| 认领时间 | 2026-10-08 02:36 |
| 分支 | `agent/local/qoder/liveness-watch/a1` |
| 状态 | 🟢 doing |

任务表对应行：`TASKS.md` → 「任务表」章节，搜 `liveness-watch`。

## 变更记录

- 2026-10-08 02:36：认领。吸收队列 ②（协调者「开2」）。机制学习：Paperclip
  `active-run-watchdog/domain/policy.ts`（silence 时钟回退链 lastOutput→processStart→
  runStart→created + 两阈值三档分类 + 抑制优先级）已在本会话逐行读过。原生
  实现落三件：①`app/liveness` 观测面（证据=租约剩余/silence 秒/零 commit 窗口，
  纯政策+扫描）；②R10 §13 L706「零 commit：连续 2 个 heartbeat 窗口无新 commit
  时告警」→ 经 alert 队列投递（一次/attempt，`source_key` 去重）；③**修一个真
  bug**：CLI worker 心跳不带 progress_hash（`cli.py:353`）、stuck 指纹对健康长跑
  全静止 → 3 个巡检周期误升级；把活跃租约（心跳续租）计入指纹，与 L706 的
  "零 commit 告警"分责。
