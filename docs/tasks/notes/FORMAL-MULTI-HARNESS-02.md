# FORMAL-MULTI-HARNESS-02

### 统一任务卡

任务编号：FORMAL-MULTI-HARNESS-02
负责人 Harness：Codex
开始时间：2026-10-15 09:00 +08:00
工作区：.worktrees/codex/
依赖任务：FORMAL-MULTI-HARNESS-01
真相源引用：R10 §17/§21/§22；AGENTS.md §1/§3/§5b
当前状态：done（实现与定点测试已在 main 上；2026-10-26 由 Mavis 接手，补上两个缺失的稳定冲突码测试并逐条核实完成标准，见「验证结果」）
commit：`2c02969`（实现随这次合并进 main）
test_id：MVP-ADMIN-REVIEW-001

| 项 | 内容 |
|---|---|
| 依赖 | FORMAL-MULTI-HARNESS-01 |
| test_id | `MVP-ADMIN-REVIEW-001` |
| 允许修改 | `backend/app/auth/policy.py`, `backend/app/api/routes.py`, `backend/app/api/schemas.py`, `backend/app/tasks/service.py`, `frontend/src/**`, `backend/tests/**` |
| 禁止修改 | `docs/spec/**`, `backend/migrations/**`, 生产项目素材与外部 Harness 工作树 |

## 完成标准

1. `MVP-ADMIN-REVIEW-001`: 隔离数据库中管理员创建项目/群组/计划并派发三个独立任务；Codex、OpenCode、Claude Code 各自以独立 Harness token claim、heartbeat/工作树、publish、submit_result，Verify 生成通过 receipt；管理员 token 对三个 attempt 分别执行 review claim 和 approve submit；三个任务均进入 `approved`，事件流中三条 approve 的 `actor_type=human` 且 `actor_id=administrator`。
2. 管理员审核必须要求 review lease 与 claimed reviewed commit；缺 lease 或 commit 不一致返回稳定冲突码。
3. Harness token 调用 `/api/v1/human/review/*` 返回 403 并写入 `permission_denied` 审计事件。
4. 前端审核操作通过 API，不能直接修改任务状态；前端单元测试和构建通过。

### 验证结果

（2026-10-26 由 Mavis 补记，OFFICE-UI-43）

| 完成标准 | 核实结果 |
|---|---|
| 1. 隔离数据库中管理员创建项目/群组/计划并派发三个独立任务；Codex、OpenCode、Claude Code 各自以独立 Harness token claim、工作树、publish、submit_result，Verify 生成通过 receipt；管理员 token 对三个 attempt 分别 review claim 与 approve；三个任务均进入 `approved`，事件流里三条 approve 的 `actor_type=human` 且 `actor_id=administrator` | ✅ **真跑**：`backend/tests/test_admin_multi_harness_review.py`（真 HTTP、真库、真工作树、真 Verify）—— 三个 Harness 各用自己的 token claim/publish/submit → 三个 attempt 到 `awaiting_review` → 管理员逐个 review claim + approve → 三个任务 `approved`；事件流里三条 approve 全是 `human`/`administrator` |
| 2. 管理员审核必须要求 review lease 与 claimed reviewed commit；缺 lease 或 commit 不一致返回稳定冲突码 | ✅ 实现**在** `submit_review`（`require_lease=True`）：`review.lease_required`、`review.commit_mismatch`、`review.lease_expired` 三个稳定码（409）。**补了测试**（原先只有 `lease_expired` 有）：新增 `backend/tests/test_review_lease_guard.py` —— 缺 lease → 409 `review.lease_required`；commit 不一致 → 409 `review.commit_mismatch`；顺带钉住「对的调用仍然过」（→ `approved`） |
| 3. Harness token 调用 `/api/v1/human/review/*` 返回 403 并写入 `permission_denied` 审计事件 | ✅ 闭环测试里断言：Harness token 调 `/human/review/claim` → 403 + `auth.permission_denied`（`bind_declared` 那一层） |
| 4. 前端审核操作通过 API，不能直接修改任务状态；前端单元测试和构建通过 | ✅ 办公室前端**没有审核操作**：审核（领取 review / 提交结论）是**管理员窗口**的动作，走平台的 `/api/v1/human/review/*`；办公室只做只读展示与转述（`admin-agent.js` 里写明）。新增 `office/scripts/verify-office-review-gate.mjs` 把它钉住：① 前端不出现 `human/review`；② 前端没有带任务状态的写请求；③「审核是管理员窗口的动作」那句说明还在（旧 `frontend/src/**` 目录已随办公室前端收进 `office/`，本卡那行「允许修改」已过时） |
| 门禁 | `tools/docstate` 98/98、`tools/discipline` 44/44、`ruff` 全绿；**全量 pytest EXIT=0**；办公室冒烟 **33/36**（3 条是与本卡无关的环境依赖：verify-anchors、verify-harness-connections、verify-project-workspace） |
| 边界 | **只加测试，不改实现**（实现的三个稳定码都在 main 上且全绿）—— 核实出实现缺口就另开卡 |
