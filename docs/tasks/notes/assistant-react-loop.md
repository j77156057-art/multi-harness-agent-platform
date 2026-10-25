# assistant-react-loop

### 统一任务卡

任务编号：assistant-react-loop
负责 Harness：Codex
开始时间：2026-10-23 13:30 +08:00
工作区：.worktrees/codex/
依赖任务：—（设计提案 docs/proposals/assistant-react-loop.md 已备，待仓库主人复核）
真相源引用：MVP-ADAPTER-003（模型通道语义与 adapter 稳定性同族；新检查 ID 待规范复审后回填真相源）；AGENTS.md §1/§2/§4/§5/§5b
当前状态：done（P1 引擎、工具白名单、prompt、反思层与机械门已验收；后续 worker/池子直连由 P2 承接）
commit：`aaa8da5`
test_id：MVP-ADAPTER-003

| 项 | 内容 |
|---|---|
| 依赖 | — |
| test_id | `MVP-ADAPTER-003` |
| 允许修改 | `backend/app/assistant/**`, `backend/tests/test_assistant_loop.py`, `docs/proposals/assistant-react-loop.md`, `docs/claims/codex--assistant-react-loop.md`, `docs/tasks/notes/assistant-react-loop.md`, `TASKS.md` |
| 禁止修改 | `docs/spec/0.3-R10.md`（真相源只读；规范条目另走复核）、`docs/traceability.md`（不得新增未登记 ID）、`backend/migrations/**`、`backend/app/admin/**`（共享在途，P2 复用时单开卡）、生产项目素材与外部 Harness 工作区 |

## 做什么

平台助手升级为项目自有的 ReAct agent（P1：引擎+工具+prompt+反思层接口，
scripted 全测）。

## 为什么

代码能干的（收集/阈值/打包）归平台代码；"能读懂但写不成规则"的
（诊断/审核助攻/排险）归助手——判据是"能不能写成 if/else"。

## 修法

见 `docs/proposals/assistant-react-loop.md` 与认领文件：白名单闭合工具表、
预算封顶、证据引用机械门、反思层 pass/revise、注入隔离、trace 收据。

## 完成标准

认领文件第 1-2 条（scripted 测试清单 + 门禁绿）。

## 状态

完成时间：2026-10-23
commit：`aaa8da5`（feat(assistant-react-loop): 平台助手升级为自有 ReAct 引擎（P1））。

### 验收结果

- `tests/test_assistant_loop.py` scripted 全绿：工具流程、引用机械门、预算、反思、trace 与注入隔离均通过。
- ruff、docstate、discipline 通过；P1 后续由 P2/P3 任务卡分别验收。
