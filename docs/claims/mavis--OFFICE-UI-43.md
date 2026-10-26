# 认领：OFFICE-UI-43

| 字段 | 值 |
|---|---|
| task_id | `OFFICE-UI-43` |
| Harness | Mavis |
| 认领时间 | 2026-10-26 08:20 +08:00 |
| 分支 | `agent/local/mavis/OFFICE-UI-43/a1` |
| 状态 | doing |

目标：**接手 `FORMAL-MULTI-HARNESS-02`**（管理员 token 真实派发三 Harness、逐项审核闭环）。

## 接手时的实况

- codex 那条分支 `agent/local/codex/FORMAL-MULTI-HARNESS-01/a1` 与 main **无差异**
  （内容已全部在 main 上）；实现与定点测试也都在 main 上：
  `/human/review/claim`、`/human/review/submit`、review lease、
  `backend/tests/test_admin_multi_harness_review.py`（= 完整闭环的脚本化真跑）。
- 卡上写「claimed（待 Merge Gate）」，验证结果那节只写了「后端定点测试与前端测试/构建
  已通过；完整门禁待本分支收口后运行」。

## 我核实到的缺口（要做的实现工作）

完成标准第 2 条「管理员审核必须要求 review lease 与 claimed reviewed commit；缺 lease
或 commit 不一致返回**稳定冲突码**」——实现**在**（`submit_review`）：
`review.lease_required`、`review.commit_mismatch`、`review.lease_expired` 三个稳定码都有；
但**只有 `review.lease_expired` 有测试**（`test_review_reclaim.py`），
`review.lease_required` 与 `review.commit_mismatch` **没有测试**。

这一张就补这两条（外加把 4 条完成标准逐条核实 + 真跑 + 结掉 codex 那张卡）。

## 范围

- `backend/tests/test_review_lease_guard.py`（**新增**，不动 codex 的测试文件）
- `docs/tasks/notes/FORMAL-MULTI-HARNESS-02.md`（状态与验证结果）
- `docs/claims/codex--FORMAL-MULTI-HARNESS-02.md`（若有）
- `TASKS.md`、`docs/traceability.md`
- `docs/claims/mavis--OFFICE-UI-43.md`、`docs/tasks/notes/OFFICE-UI-43.md`

## 怎么做到不出错

1. **只加测试，不改实现**（实现的三个稳定码都在 main 上且全绿）—— 若核实发现实现有缺口，
   另开卡，不在补测试时顺手改。
2. 补的测试要**真跑**（真 HTTP、真库、真工作树），不是 mock。
3. 不改 codex 的测试文件（那是他的在途范围）。
