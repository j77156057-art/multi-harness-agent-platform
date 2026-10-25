# 认领：OFFICE-UI-39

| 字段 | 值 |
|---|---|
| task_id | `OFFICE-UI-39` |
| Harness | Mavis |
| 认领时间 | 2026-10-26 06:25 +08:00 |
| 分支 | `agent/local/mavis/OFFICE-UI-39/a1` |
| 状态 | doing |

目标：**接手 codex 的在途**，收尾 `pool-free-set-watch`（免费档目录变更哨）。

## 接手时的实况

实现与 7 条 scripted 测试**已在 main 上**（`9d73000`），入口
`python -m app.pool.free_set_watch [--once|--loop]` 也已备好。卡上唯一没做的是
**部署**：哪个进程跑 `--loop`（或 cron 打 `--once`），这一条卡里写着「待仓库主人定」。

## 这一张做三件事

1. **核实**：7 条 scripted 测试在 main 上且绿；模块与入口可用。
2. **真跑**：真的跑两遍 `--once`（首跑建基线不喊人；第二遍同名单静默）。
3. **把部署这件事摆到主人面前**：这是运维决定，不替他定 —— 但把选项、影响与
   「不部署会怎样」写清楚，让他一句话就能定。

## 范围

- `docs/tasks/notes/pool-free-set-watch.md`（状态、验证结果、部署选项）
- `docs/claims/codex--pool-free-set-watch.md`（补记核实结论）
- `TASKS.md`
- `docs/claims/mavis--OFFICE-UI-39.md`、`docs/tasks/notes/OFFICE-UI-39.md`

## 怎么做到不出错

1. **不擅自常驻**：哨会**发告警**（outbound 性质），挂不挂、挂哪儿是运维决定。
   这一张只把入口与选项核实清楚，部署等主人一句话。
2. 不碰实现（代码在 main 上且绿）。
3. 真跑用 `--once`，不启动 `--loop`（别在别人机器上留个常驻进程）。

## 完成标准

1. 7 条 scripted 测试绿 + 入口可用 + 真跑两遍的记录。
2. 卡上写清部署的三个选项与各自的影响，等主人定。
3. 门禁全绿；`docs/traceability.md` 登记收尾。
