# 认领：pool-free-set-watch

| 字段 | 值 |
|---|---|
| task_id | `pool-free-set-watch` |
| Harness | Codex |
| 认领时间 | 2026-10-23 18:10 +08:00（提案登记）；2026-10-23 19:00 前后经仓库主人指示"继续完成其他的"开工 |
| 分支 | `agent/local/codex/OFFICE-UI-01/a1` |
| 状态 | doing（实现＋7 条 scripted 已就位，本轮提交；部署时机〔--loop 挂哪个进程/cron〕待仓库主人定） |

## 做什么
免费档目录变更哨：快照 diff + alerts 现成投递；被动为主，不主动探测。
实现为独立入口 `python -m app.pool.free_set_watch`（sweeper 家族禁止
outbound，故不挂 run_all_sweeps）。

## 边界
不新增表/事件/路由；新检查 ID 待规范复审；alerts/scheduler 共享文件
同轮零修改。

## 完成标准
卡里 scripted 七条（首跑基线/静默/增删各一条/重查幂等/回滚重演不重投/
坏快照不误报/点名刷新）。
