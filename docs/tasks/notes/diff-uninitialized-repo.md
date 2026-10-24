# diff-uninitialized-repo

### 统一任务卡

任务编号：diff-uninitialized-repo
负责 Harness：Codex
开始时间：2026-10-23 11:20 +08:00
工作区：.worktrees/codex/
依赖任务：—
真相源引用：MVP-UI-002（diff 消息端点，`available:false` 不发明数据）；AGENTS.md §5b/H-8（共享文件：`backend/app/tasks/service.py` 另有归属，单开卡）
当前状态：doing（实现与定点测试已就位；待合并门禁）
test_id：MVP-UI-002

| 项 | 内容 |
|---|---|
| 依赖 | — |
| test_id | `MVP-UI-002` |
| 允许修改 | `backend/app/git/repo.py`, `backend/app/tasks/service.py`, `backend/tests/test_task_diff.py`, `docs/claims/codex--diff-uninitialized-repo.md`, `docs/tasks/notes/diff-uninitialized-repo.md`, `TASKS.md` |
| 禁止修改 | `docs/spec/0.3-R10.md`（真相源只读）、`backend/migrations/**`、生产项目素材、`.worktrees/` 其他 Harness 工作区（**不动共享根目录**） |

## 做什么

`GET /tasks/{id}/diff?include_patch=true` 在 `AP_CANONICAL_REPO` 指向不存在的
目录时回 500；改成老实回 `available: false`（按"没有改动"对待），路由 200。

## 修法

- `CanonicalRepo.has_object`：git 进不起（OSError，WinError 267）＝False；
- `diff_for_task`：仓库未就位（`is_initialized()`＝False）时
  `reason = "canonical repository is not initialized"`，不编造"提交不在库里"。

## 完成标准

见认领文件：2 条新回归 + 既有 `test_task_diff.py` / `test_publish.py`
全绿 + ruff/docstate/discipline 绿。

## 状态

实现与定点测试已就位。分支 `agent/local/codex/OFFICE-UI-01/a1`，
提交：`59f9f45`（fix(diff-uninitialized-repo)，实现、登记与回归测试
同一次提交）。
