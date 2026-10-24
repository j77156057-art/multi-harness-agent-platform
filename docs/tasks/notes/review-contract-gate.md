# review-contract-gate

### 统一任务卡

任务编号：review-contract-gate
负责 Harness：Codex
开始时间：2026-10-24
工作区：.worktrees/codex/
依赖任务：assistant-react-loop-p3
真相源引用：R10 §9（计划验收契约）、§21（审核）；AGENTS.md §1/§2/§4/§5/§5b
当前状态：done（实现与验收完成）
commit：待本次提交回填
test_id：`MVP-ADAPTER-003`（不新增检查 ID）

| 项 | 内容 |
|---|---|
| 依赖 | assistant-react-loop-p3 |
| test_id | `MVP-ADAPTER-003` |
| 允许修改 | `backend/app/tasks/service.py`、review/plan 测试、`docs/proposals/review-contract-gate.md`、本卡、认领、`TASKS.md` |
| 禁止修改 | migrations、事件/路由/权限键、`docs/spec/0.3-R10.md`、`docs/traceability.md` |

## 做什么

机械确保 approve findings 逐条覆盖 task plan snapshot 中的验收条款；拒绝未知
契约 id。计划发布时缺失或非法 `check` 拒绝，沿用已有 `PlanValidator`。

## 裁决

仓库主人裁决 A+B 都做；缺少 `check` 的验收条款拒绝发布。显式
`no_evidence: true` 表示该条仍需人工查验，但必须明确关联 `contract_id`；
`request_changes` 不要求完整覆盖。

## 验收结果

- 定点 review/plan/API 测试通过。
- 全量 pytest：`1036 passed`。
- ruff check、ruff format、docstate `98/98`、discipline `44/44` 全绿。
