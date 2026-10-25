# 认领：OFFICE-UI-42

| 字段 | 值 |
|---|---|
| task_id | `OFFICE-UI-42` |
| Harness | Mavis |
| 认领时间 | 2026-10-26 07:30 +08:00 |
| 分支 | `agent/local/mavis/OFFICE-UI-42/a1` |
| 状态 | doing |

目标：**把 free-set-watch 的部署方式定了**（上一张卡留下的最后一个未决项）。

## 调查到的事实

1. 哨与投递是**两个独立进程**：
   - `python -m app.pool.free_set_watch` 只**入队**（`alerts_enqueued`），不投递；
   - `python -m app.alerts` 才**投递**，收件人是 `AP_ALERT_WEBHOOK_URL`（HTTPS）。
2. **不能挂进平台进程**：平台有「sweeps 永不做 outbound（连告警排队都不做）」的不变量，
   哨是独立入口才合规矩。
3. **本机没有告警读面**（`routes.py` 里没有 alerts 的读端点），也没配 webhook
   → 装了哨，告警会入队，但**没人收、也没处看**。
4. 队列在 `alert_deliveries` 表（`UNIQUE(kind, source_key)` 幂等，不会无限堆积）。

## 这一张定的事

- **部署方式定案**：两个独立进程，与平台进程并列（不是 Windows 计划任务 —— 那是这台
  机器的事，不是仓库的事）：
  - 哨：`python -m app.pool.free_set_watch --loop --interval 900`
  - 投递：`python -m app.alerts --loop --interval 60`
- **安装门槛写清楚**：没有收件人（`AP_ALERT_WEBHOOK_URL`）就不装 —— 装了是自欺欺人。
- **写进仓**：`docs/deploy-free-set-watch.md`（怎么起、怎么停、怎么排错、账在哪看）。
- **本机验证整条链**：故意让名单变一次 → 哨入队 → `alert_deliveries` 里出现一条 →
  投递端按规则处理（没有 webhook 就**明说**跳过，不假装投出去了）。

## 范围

- `docs/deploy-free-set-watch.md`（新增）
- `docs/tasks/notes/pool-free-set-watch.md`（「部署」段从三个待定选项改成定案）
- `docs/claims/codex--pool-free-set-watch.md`、`TASKS.md`、`docs/traceability.md`
- `docs/claims/mavis--OFFICE-UI-42.md`、`docs/tasks/notes/OFFICE-UI-42.md`

## 怎么做到不出错

1. **不改实现**（代码在 main 上且绿）。
2. **不擅自留常驻进程**：本机只手动跑一次做验证；常驻由主人决定何时装。
3. **不替主人选收件人**：收件人是他的接收端，缺它就把门槛说清楚，不假装装好了。
