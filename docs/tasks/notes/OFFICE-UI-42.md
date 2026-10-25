# OFFICE-UI-42

### 统一任务卡

任务编号：OFFICE-UI-42
负责 Harness：Mavis
开始时间：2026-10-26 07:30 +08:00
工作区：`.worktrees/mavis/`
依赖任务：OFFICE-UI-31（助手的深取证档）—— 与 UI-32~40 同一层：UI-33 已在链长 9 的
末端，再挂上去会超过清单 §0.2 声明的长度
真相源引用：`backend/app/pool/free_set_watch.py`（哨）、`backend/app/alerts/service.py`
（投递）、`backend/app/alerts/__main__.py`（投递入口）、
`docs/tasks/notes/pool-free-set-watch.md`（被收尾的那张卡）、
`docs/dev-convention.md` H-4 / H-7 / H-8
当前状态：done（已合入 main：`56bd334`）
commit：`IMPLEMENT_COMMIT_PLACEHOLDER`（free-set-watch 的部署方式定案）
验证结果：见文末「验证结果」表。
test_id：MVP-ADAPTER-005

| 项 | 内容 |
|---|---|
| 依赖 | OFFICE-UI-31 |
| test_id | `MVP-ADAPTER-005` |
| 允许修改 | `docs/deploy-free-set-watch.md`（新增）、`docs/tasks/notes/pool-free-set-watch.md`、`docs/claims/codex--pool-free-set-watch.md`、`TASKS.md`、`docs/traceability.md`、`docs/claims/mavis--OFFICE-UI-42.md`、`docs/tasks/notes/OFFICE-UI-42.md` |
| 禁止修改 | `backend/app/**`（实现已在 main 上）、`docs/spec/**`、其他 Harness 的工作区与分支 |

## 做什么

| 原来 | 现在 |
|---|---|
| free-set-watch 的部署方式三个选项待主人定 | **定案**：哨与投递各是一个独立进程（与平台进程并列），命令与间隔写死；**安装门槛**（收件人）写清；落成 `docs/deploy-free-set-watch.md`；本机验证整条链（入队 → 账里有记录 → 投递端按规则处理） |

## 为什么

仓库主人圈了「先把 free-set-watch 的部署方式定了」。这是上一张卡留下的最后一个未决项。

## 怎么做到不出错

1. 不改实现。
2. 不擅自留常驻进程（本机只手动跑一次做验证）。
3. 不替主人选收件人 —— 缺它就说清楚，不假装装好了。

## 完成标准

1. 部署方式定案并写进仓（怎么起、怎么停、怎么排错、账在哪看）。
2. 本机验证整条链。
3. 门禁全绿。

## 验证结果

| 门禁 | 结果 |
|---|---|
| 部署方式定案 | **两个独立进程，与平台进程并列**：哨 `python -m app.pool.free_set_watch --loop --interval 900`（只入队）、投递 `python -m app.alerts --loop --interval 60`（HMAC 签名投递）。**不挂进平台进程** —— 平台有「sweeps 永不做 outbound」的不变量，哨是独立入口才合规矩。**不用计划任务/cron** —— 那是某台机器的事，仓库只给「跑什么、跑多久、怎么停」 |
| 安装门槛 | 投递端要 `AP_ALERT_WEBHOOK_URL`（HTTPS）+ `AP_ALERT_WEBHOOK_SECRET`；**没有收件人就别装**（装了只是「变了记录在本地账里、没人知道」）。本仓库**没有告警读面**，队列只能直接查 `alert_deliveries` 表 |
| 落地说明 | 新增 `docs/deploy-free-set-watch.md`：是什么/不是什么、两个进程怎么起、怎么停、账在哪看与三种状态、排错表、一次整链验证记录 |
| **本机整链验证** | 在快照里加一个不存在的名字（模拟「供应商新增了免费模型」）→ **哨** `{"changed": true, "first_snapshot": false}` → **账** 里多了一条 `kind=pool.free_set_changed source_key=a9b68f20… status=pending attempts=0` → **投递端** `{"configured": false, "detected": 0, "delivered": 0, "failed": 0, "pending": 1}`（本机没配收件人 → **明说**没投，不假装投出去了）→ 快照已还原 |
| `tools/docstate` / `tools/discipline` | 98/98、44/44 |
| `pytest`（全量） | EXIT=0（本卡**只改文档**；后端代码逐字节未变，全量已在 OFFICE-UI-40 那一轮跑过并 EXIT=0） |
| 边界 | **没有改它的实现**；**没有擅自留常驻进程**（本机只手动跑一次做验证）；**没有替主人选收件人** |
