# 认领：review-contract-gate

| 字段 | 值 |
|---|---|
| task_id | `review-contract-gate` |
| Harness | Codex |
| 认领时间 | 2026-10-24 10:00 |
| 分支 | `agent/local/codex/OFFICE-UI-01/a1` |
| 状态 | done（A+B 已实施并验收；commit `10a50bb`） |

## 裁决

- A+B 都做。
- 验收条款缺少 `check` 时拒绝发布，不采用默认 `human`。
- 不新增表、路由、事件、权限键或检查 ID。

## 实现范围

- approve findings 通过 `contract_id` 覆盖任务绑定快照中的每个 acceptance id。
- 缺少覆盖时报 `review.contract_missing`；未知条款时报 `review.contract_unknown`。
- `no_evidence: true` 是明确覆盖标记，仍需附说明；`request_changes` 不要求覆盖。
- B 由现有 `PlanValidator` 拒绝缺失/非法的 `check`，本任务不重复实现。

## 验收结果

- 定点 review/plan/API 测试通过。
- 全量 pytest：`1036 passed`。
- ruff check、ruff format、docstate `98/98`、discipline `44/44` 全绿。
