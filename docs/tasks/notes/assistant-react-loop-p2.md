# assistant-react-loop-p2

### 统一任务卡

任务编号：assistant-react-loop-p2
负责 Harness：Codex
开始时间：2026-10-23 16:40 +08:00
工作区：.worktrees/codex/
依赖任务：assistant-react-loop（P1，提交 `aaa8da5`）
真相源引用：MVP-ADAPTER-003（模型通道/CLI 槽消费语义同族；新检查 ID 不自造，待规范复审）；AGENTS.md §1/§2/§4/§5/§5b
当前状态：doing
test_id：MVP-ADAPTER-003

| 项 | 内容 |
|---|---|
| 依赖 | assistant-react-loop（P1） |
| test_id | `MVP-ADAPTER-003` |
| 允许修改 | `backend/app/assistant/**`, `backend/tests/test_assistant_*.py`, `docs/claims/codex--assistant-react-loop-p2.md`, `docs/tasks/notes/assistant-react-loop-p2.md`, `docs/proposals/assistant-react-loop.md`, `TASKS.md` |
| 禁止修改 | `backend/app/admin/**`（共享在途；只 import `PlatformClient` 复用，零修改）、`docs/spec/0.3-R10.md`、`docs/traceability.md`（无新 ID）、`backend/app/auth/**`（核实结论：矩阵零行新增，不改）、`backend/migrations/**`、生产项目素材与外部 Harness 工作区 |

## 做什么

P1 引擎接真模型（池子直连免费档五家，零 USD）与真私聊（assistant worker
从 admin/worker 削薄壳）。

## 为什么

免费池五家都是 CLI 槽，池子 `_cli_chat` 已能把 OpenAI 兼容请求转给它们并
记收据（实测 usage_reported: true）；metered（0.16879 USD）不动。权限面
读路径已是公共读、`harness:pool` 键已放行全体 harness——矩阵无需加行。

## 修法

见认领文件"做什么"三条：model.py（链式回退+收据）、worker.py（轮询→
围栏进引擎→私聊交付，诚实异常）、loop.py 小改（上游收据进 trace，
不本地估）。

## 完成标准

认领文件第 1-4 条。

## 状态

登记完毕，实现中。提交：`a6cdf57`（feat(assistant-react-loop-p2)：助手接通池子直连免费档与私聊自循环）。
