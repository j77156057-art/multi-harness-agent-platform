### agent-onboarding：让其他 AI 知道要在隔离工作区干活（done）

~~~text
任务编号：agent-onboarding
负责人 Harness：Claude Code
开始时间：2026-10-05 07:41
工作区：.worktrees/claude/
依赖任务：worktree-isolation（✅ 已合入 main `247ddc7`）
真相源引用：docs/dev-convention.md H-7；docs/spec/mvp-task-checklist.md H-5
允许修改目录：AGENTS.md、CLAUDE.md、README.md、TASKS.md、
            docs/prep/README.md、docs/spec/mvp-task-checklist.md
禁止修改目录：backend/**、docs/spec/0.3-R10.md（只读）、tools/docstate/**
交付分支：agent/local/claude/agent-onboarding/a1
test_id：MVP-PROCESS-001
当前状态：已实现，**合入 main 失败（并发 ref 锁争用），尚未合入**
commit：a6ec7ae
已知问题：
- ⚠️ main 上仍无 `AGENTS.md`，其他 AI 还看不到入口文件
- ⚠️ 合入曾三次撞上 `couldn't set refs/...` / `ORIG_HEAD` ref 锁争用
~~~
