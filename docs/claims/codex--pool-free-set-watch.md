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

## 记录

- 2026-10-23（烟测）：`--once` 建基线 5 家（指纹 `e349cd68…`）。
- 2026-10-23（上岗实锤，cli-roster-enumerate 落地后）：`--once` 检出
  免费档 5→13 家（opencode-cli 槽 1→9），恰好入队 1 条真告警，
  快照翻新（指纹 `f844659e…`）——哨按声明的被动原则只读目录 diff，
  这次告警就是它干活的证明。部署方式（--loop 挂哪个常驻进程/cron）
  待仓库主人拍板。

## 2026-10-26：核实并真跑（OFFICE-UI-39，Mavis）

实现与 7 条 scripted 已在 main 上并全绿；真跑两遍 `--once`（首跑建基线不喊人、
同名单静默）。**部署时机待仓库主人定**，三个选项与代价见
`docs/tasks/notes/pool-free-set-watch.md` 的「部署」段。
