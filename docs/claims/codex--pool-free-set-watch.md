# 认领：pool-free-set-watch

| 字段 | 值 |
|---|---|
| task_id | `pool-free-set-watch` |
| Harness | Codex |
| 认领时间 | 2026-10-23 18:10 +08:00（提案登记） |
| 分支 | `agent/local/codex/OFFICE-UI-01/a1` |
| 状态 | waiting（**待仓库主人复核**；提案 docs/proposals/pool-free-set-watch.md；未写代码） |

## 做什么
免费档目录变更哨：快照 diff + alerts 现成投递；被动为主，不主动探测。

## 边界
不新增表/事件/路由；新检查 ID 待规范复审；alerts 共享文件同轮零修改。

## 完成标准
提案文末 scripted 三条（同目录不告警；增删换各一条；幂等）。
