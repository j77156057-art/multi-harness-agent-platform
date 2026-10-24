# 认领：diff-uninitialized-repo

| 字段 | 值 |
|---|---|
| task_id | `diff-uninitialized-repo` |
| Harness | Codex |
| 认领时间 | 2026-10-23 11:20 +08:00 |
| 分支 | `agent/local/codex/OFFICE-UI-01/a1` |
| 状态 | doing（修复与回归测试已就位；`backend/app/tasks/service.py` 是共享文件另有归属，故单开任务卡，未动共享根目录，待合并门禁排队） |

## 做什么
`GET /tasks/{id}/diff?include_patch=true` 在正式仓库路径不对
（`AP_CANONICAL_REPO` 指向不存在的目录）时回 500；改成老实回
`available: false`（读者按"没有改动"对待），路由 200。

## 为什么（根因，机械可判）
- `CanonicalRepo.has_object` 用 `subprocess.run(..., cwd=self.path)`；
  目录不存在时 Windows 直接抛 `NotADirectoryError [WinError 267]`
  （本机复现脚本留档），它只挡了 `returncode` 没挡 OSError；
- 异常从 `diff_for_task`（`backend/app/tasks/service.py:1864`）一路冒到
  FastAPI 路由 → 500。

## 修法（不猜）
- `has_object`：git 起不来（OSError）＝"看不到"＝False，与"不可达"同义，
  失败关闭；`publish` 侧同函数因此说 `commit_unreachable` 也是诚实方向；
- `diff_for_task`：先问 `is_initialized()`（与 `ensure()` 同一判据）；
  仓库没就位时 `reason = "canonical repository is not initialized"`——
  **不编**"commits are not in the canonical repository"（那句话自称知道
  提交的去向，实际连问都没问成）；`files/patch` 为空，`available: false`。

## 边界
- `backend/app/tasks/service.py` 为共享文件（FORMAL-MULTI-HARNESS-02、
  R10-fix-17、R10-fix-18、review-provider-record 四张在途卡都列出它）；
  按 H-8 只在自己 worktree 改、单开卡登记，等合并门禁把这个提交带上 main。
- 不动 schema / 事件名 / 状态 / 迁移边；`schema_version` 不动。

## 完成标准（机械可判）

1. `tests/test_task_diff.py` 新增 2 条绿：
   `test_a_store_that_cannot_be_opened_answers_false_not_an_exception`、
   `test_an_uninitialized_canonical_repo_is_unavailable_not_a_500`
   （服务层 reason + 路由 200 + patch 为空）；
2. 既有回归不破：`test_task_diff.py`、`test_publish.py` 全绿（与
   `has_object` 共用的 publish 路径回归）；
3. `ruff`（check + format）全绿；`tools/docstate`、`tools/discipline` 绿。
