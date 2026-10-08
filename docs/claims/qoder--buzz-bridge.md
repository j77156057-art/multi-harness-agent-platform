# 认领：buzz-bridge

<!-- 本文件由 Qoder 创建。其他 AI 请勿修改本文件；改你自己的即可，永不冲突。 -->

| 字段 | 值 |
|---|---|
| task_id | `buzz-bridge` |
| Harness | Qoder |
| 认领时间 | 2026-10-09 03:10 |
| 分支 | `agent/local/qoder/buzz-bridge/a1` |
| 状态 | 🚧 doing |

任务表对应行：`TASKS.md` → 「任务表」章节，搜 `buzz-bridge`。

## 变更记录

- 2026-10-09 03:10：认领（路线图第 5 项主体；ADR-DEV-005 落成后开工）。实现
  ADR-DEV-005 的运行时：`app/buzz`（游标增量读 `task_events` → 字段白名单投影
  → NIP-01 事件签名（coincurve/libsecp256k1，BIP340 向量交叉验证过）→ WS +
  NIP-42 AUTH 发布 kind:9 + `["h", channel]`）；迁移 013 `buzz_cursors`（抄
  `alert_cursors` 形状）；`AP_BUZZ_*` 未配置=显式 no-op。**验收通道经协调者
  确认：本地自建 relay**（测试内起最小 NIP-01/42 relay 真 WS 端到端 + 演练区
  真 CLI 实跑）；ADR 状态仍=提议（不影响本卡按其约束实现——"任何双向需求
  另立 ADR"的铁律照办）。
