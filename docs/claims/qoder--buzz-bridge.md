# 认领：buzz-bridge

<!-- 本文件由 Qoder 创建。其他 AI 请勿修改本文件；改你自己的即可，永不冲突。 -->

| 字段 | 值 |
|---|---|
| task_id | `buzz-bridge` |
| Harness | Qoder |
| 认领时间 | 2026-10-09 03:10 |
| 分支 | `agent/local/qoder/buzz-bridge/a1` |
| 状态 | ✅ done（实现 0e6e24b 待提交；本地自建 relay 真 CLI 验收留档） |

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
- 2026-10-09 03:35：实现完成并全绿。`test_buzz_bridge.py` 16 测试（测试内真
  WS relay：全链/顺序/投影不泄 payload/游标推进与 no-op/relay 挂→游标不动→
  重试+按 id 去重/relay 策略拒绝/强制 AUTH 对照组/BIP340 向量 #0 逐字节/事件
  id 重算与篡改/CLI 三态+坏配置）；全量门禁 `529 passed`（513+16）。演练区
  `backend/data/buzz-drill/`（gitignored）独立 relay 进程 + 真 CLI `--once`
  实跑留档：`relay-messages.jsonl`（10 行 EVENT = 5 唯一 id + 5 duplicate）、
  `relay-stdout.log`。演练中发现并修复一个真 bug（空批次 `scanned` 误报 1 →
  0，已补断言）；另确认一个演练脚本侧的 Windows 瞬时文件锁（改为长句柄，
  非应用 bug）。
