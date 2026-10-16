# FORMAL-MULTI-HARNESS-02

### 统一任务卡

任务编号：FORMAL-MULTI-HARNESS-02
负责人 Harness：Codex
开始时间：2026-10-15 09:00 +08:00
工作区：.worktrees/codex/
依赖任务：FORMAL-MULTI-HARNESS-01
真相源引用：R10 §17/§21/§22；AGENTS.md §1/§3/§5b
当前状态：claimed（待 Merge Gate）
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

后端定点测试与前端测试/构建已通过；完整门禁待本分支收口后运行。
