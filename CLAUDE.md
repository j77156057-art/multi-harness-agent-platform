# CLAUDE.md

本项目的全部作业规则在 **[`AGENTS.md`](AGENTS.md)**。

在编辑任何文件之前请先读它，尤其是：

- **不要在仓库根目录 `new agent/` 编辑文件** —— 改用 `.worktrees/<harness>/`，
  否则会因多个 AI 共享工作区而丢失提交（已发生 4 次）。
- **认领必须先于开工** —— 先在 `TASKS.md` 登记，再动手。
- **提交前 `./scripts.sh check` 三道门禁必须全绿**，不许绕过。

细节与硬约束 H-1～H-7 见 `docs/dev-convention.md`；权威规范（只读）
是 `docs/spec/0.3-R10.md`。