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
当前状态：done
commit：`0916c2e`（接手 FORMAL-MULTI-HARNESS-02：补两条缺失的冲突码测试 + 4 条完成标准逐条核实 + 真跑）
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
| 逐条核实（4 条完成标准） | **4/4 通过**：① 闭环真跑（三个 Harness 各自独立 token → 三个 attempt 到 `awaiting_review` → 管理员逐个 review claim + approve → 三个任务 `approved`，事件流三条 approve 全是 `human`/`administrator`）② 审核要 review lease + claimed commit，三个稳定冲突码都在（`review.lease_required` / `review.commit_mismatch` / `review.lease_expired`）③ Harness token 调审核接口 403 + `auth.permission_denied` ④ 办公室前端**没有**审核操作（审核是管理员窗口的动作，走平台 API），也没有带任务状态的写请求 |
| 新增测试 | `backend/tests/test_review_lease_guard.py` **3 项**（原先只有 `lease_expired` 有测试）：缺 lease → 409 `review.lease_required`；commit 不一致 → 409 `review.commit_mismatch`；对的调用仍然过（→ `approved`）。真 HTTP、真库、真工作树 |
| 真跑（闭环） | `tests/test_admin_multi_harness_review.py` + `test_review_findings.py` + `test_review_reclaim.py` + 新增的 `test_review_lease_guard.py` 全绿 |
| 前端归属 | 新增 `office/scripts/verify-office-review-gate.mjs`（进冒烟清单）：前端不出现 `human/review`、没有带任务状态的写请求、「审核是管理员窗口的动作」那句说明还在。旧的 `frontend/src/**` 目录已随办公室前端收进 `office/`，本卡那行「允许修改」已过时 |
| `tools/docstate` / `tools/discipline` / `ruff` | 98/98、44/44、全绿 |
| 办公室冒烟 | 33/36（3 条是与本卡无关的环境依赖） |
| `pytest`（全量） | EXIT=0 |
| 边界 | **只加测试，不改实现**；核实出实现缺口就另开卡 |
