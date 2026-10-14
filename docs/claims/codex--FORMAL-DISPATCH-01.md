# 认领：FORMAL-DISPATCH-01

| 字段 | 值 |
|---|---|
| task_id | `FORMAL-DISPATCH-01` |
| Harness | Codex |
| 认领时间 | 2026-10-14 19:37 +08:00（登记写入时刻，实施等待合入） |
| 分支 | `agent/local/codex/FORMAL-DISPATCH-01/a1` |
| 状态 | done（登记已合入 1ff750e；实现与自检完成；业务任务仍待人审） |

范围、独占路径、test_id 与完成标准见对应任务卡。正式注册/领取只走 API；
不向别人的聊天发送指令、不假定其他 Harness 已领取。

实跑任务 `FD-aec39028dacb9e7d`：真实 CLI 提交、baseline 1/head 0、awaiting_review。
最终全量 pytest 540 passed / 1 dependency warning，docstate 90/90、discipline 44/44、Ruff 通过。
详见 docs/reports/formal-dispatch/DELIVERY.md 与 VALIDATION.md。
