# PUBLIC-CLEAN-01

### 统一任务卡

任务编号：PUBLIC-CLEAN-01
负责人 Harness：Mavis
开始时间：2026-10-26 13:18 +08:00
工作区：`.worktrees/mavis-public-clean`
依赖任务：—
真相源引用：`README.md`、`AGENTS.md`、`docs/dev-convention.md` H-7/H-8
当前状态：doing
交付分支：`agent/workspace/mavis-public-clean`

test_id：`MVP-PROCESS-001`

## 目标

移除 GitHub 当前公开快照中的内部协作、任务、准备、验收、事故文档与本机路径配置。保留历史，不改写 Git 历史，不 force push。

## 范围

- 移除任务登记、认领、任务卡、准备草案、内部运行报告与事故记录。
- 删除或改写含本机绝对路径的公开环境配置示例。
- 保留项目源码、必要测试、README、API/协议规范及面向项目读者的架构决策。

## 完成标准

- Git 当前快照中不再包含 `TASKS.md`、`AGENTS.md`、`CLAUDE.md`、内部 claims/tasks/prep/reports/incidents 资料。
- 跟踪文件中不出现本机绝对路径或真实凭据。
- 环境模板仅包含占位或相对配置，不包含本机用户目录。
- `docstate`、`discipline`、`git diff --check` 通过；可用时运行完整仓库检查。
- 通过受控合并与普通非强制 push 更新 GitHub `main`。

## 验证结果

待执行。
