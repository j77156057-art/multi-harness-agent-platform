# OFFICE-UI-43

### 统一任务卡

任务编号：OFFICE-UI-43
负责 Harness：Mavis
开始时间：2026-10-26 08:20 +08:00
工作区：`.worktrees/m42`
依赖任务：OFFICE-UI-31（助手的深取证档）—— 与 UI-32~42 同一层：UI-33 已在链长 9 的
末端，再挂上去会超过清单 §0.2 声明的长度
真相源引用：`backend/app/tasks/service.py`（`submit_review` 的 lease 与 commit 校验）、
`backend/app/api/routes.py`（`/human/review/claim`、`/human/review/submit`）、
`backend/tests/test_admin_multi_harness_review.py`（闭环的脚本化真跑）、
`docs/tasks/notes/FORMAL-MULTI-HARNESS-02.md`（被接手的卡）、
`docs/dev-convention.md` H-4 / H-7 / H-8
当前状态：doing
验证结果：见文末「验证结果」表。
test_id：MVP-ADMIN-REVIEW-001

| 项 | 内容 |
|---|---|
| 依赖 | OFFICE-UI-31 |
| test_id | `MVP-ADMIN-REVIEW-001` |
| 允许修改 | `backend/tests/test_review_lease_guard.py`（**新增**）、`docs/tasks/notes/FORMAL-MULTI-HARNESS-02.md`、`TASKS.md`、`docs/traceability.md`、`docs/claims/mavis--OFFICE-UI-43.md`、`docs/tasks/notes/OFFICE-UI-43.md` |
| 禁止修改 | `backend/app/**`（实现已在 main 上；核实出缺口就另开卡）、`backend/tests/test_admin_multi_harness_review.py`（codex 的在途）、`backend/tests/test_review_reclaim.py`、`docs/spec/**`、其他 Harness 的工作区与分支 |

## 做什么

| 原来 | 现在 |
|---|---|
| `FORMAL-MULTI-HARNESS-02` 停在 claimed（待 Merge Gate），完成标准第 2 条的两个稳定冲突码（`review.lease_required`、`review.commit_mismatch`）**没有测试** | 补上这两条测试；4 条完成标准逐条核实 + 真跑；把 codex 那张卡结掉 |

## 为什么

仓库主人圈了「b」：直接把 FORMAL-MULTI-HARNESS-02 接过来做完（另一个会话只做到认领）。

## 怎么做到不出错

1. 只加测试，不改实现。
2. 补的测试要真跑。
3. 不动 codex 的测试文件。

## 完成标准

1. 两条缺失的冲突码有测试（真 HTTP、真库）。
2. 4 条完成标准逐条核实 + 真跑。
3. codex 那张卡结掉；门禁全绿。

## 验证结果

| 门禁 | 结果 |
|---|---|
| 逐条核实（4 条完成标准） | 待回填 |
| 新增测试 | 待回填 |
| 真跑（闭环） | 待回填 |
| `tools/docstate` / `tools/discipline` / `ruff` | 待回填 |
| `pytest`（全量） | 待回填 |
