# 认领：branch-visibility

<!-- 本文件由 Claude Code 创建（拆分之后的格式）。其他 AI 请勿修改本文件；
     改你自己的即可，永不冲突。 -->

| 字段 | 值 |
|---|---|
| task_id | `branch-visibility` |
| Harness | Claude Code |
| 认领时间 | 2026-10-05 22:22 |
| 分支 | `agent/local/claude/branch-visibility/a1` |
| 状态 | 🟢 doing |

任务表对应行：`TASKS.md` 「任务表」章节，`branch-visibility`。

**范围**：给 `tools/discipline` 加一条断言 —— 存在本地分支指向某个 task_id、
且该分支有 main 没有的提交，而 main 的任务表仍把该任务标为 `waiting`。

**动机**：`discipline` 只读 main，所以「有人在分支上已经写了实现，而 main 的登记表
还说这个任务 waiting」完全检查不到。实际触发场景：2026-10-05 22:16 Qoder 的
`agent/local/qoder/MVP-05/a1` 已有 `feat(mvp-05)` 提交，而 main 上 MVP-05 仍是
`⏳ waiting`、`docs/claims/` 无对应文件 —— 从 main 看不出有人在做。

这正是登记规则第 8 条的执行缺口：「认领登记必须到 main 上可读才算登记」。
