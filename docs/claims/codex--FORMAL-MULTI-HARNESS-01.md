# 认领：FORMAL-MULTI-HARNESS-01

| 字段 | 值 |
|---|---|
| task_id | `FORMAL-MULTI-HARNESS-01` |
| Harness | Codex |
| 认领时间 | 2026-10-15 00:00 +08:00（本轮开工登记） |
| 分支 | `agent/local/codex/FORMAL-MULTI-HARNESS-01/a1` |
| 状态 | blocked |

本任务只测试和修复本机正式 API、三个已有 CLI 适配器及其 worker 选择逻辑。Claude Code 两次真实任务都能创建限定文件，但 Git 操作均要求人工批准 linked worktree 的 `.git` 重定向；为当前 attempt 增加专属 gitdir `--add-dir` 后仍要求人工批准。未启用 `bypassPermissions`，等待协调者在 Claude 提示中逐命令批准，或裁定由平台代提交。证据：`data/formal-runs/FD-d7ab907e8689defd.failure.json`、`data/formal-runs/FD-173b34a1f4ce3dd1.failure.json`。任务分工
通过正式 dispatch 的单 Harness `harness_id` 表达，不扩展 R10 状态机，也不把人工审查
或生产验收伪装成 CLI 结果。
