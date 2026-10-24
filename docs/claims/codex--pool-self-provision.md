# 认领：pool-self-provision

| 字段 | 值 |
|---|---|
| task_id | `pool-self-provision` |
| Harness | Codex |
| 认领时间 | 2026-10-23 22:40 +08:00 |
| 分支 | `agent/local/codex/OFFICE-UI-01/a1` |
| 状态 | done |
| test_id | `MVP-ADAPTER-003` |

## 认领范围

实现只读候选发现与 alerts 入队；不修改 harness 注册路由，不新增池表或权限，
不自动写 `data/pool/config.json`。免费档必须有明确声明或现有 enumerate 证据，
未知情况一律输出 `unknown` 并交给人工决定。
