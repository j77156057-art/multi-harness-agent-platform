# assistant-react-loop-p3

### 统一任务卡

任务编号：assistant-react-loop-p3
负责 Harness：Codex
开始时间：2026-10-23 18:36 +08:00
工作区：.worktrees/codex/
依赖任务：assistant-react-loop（P1 引擎）、assistant-react-loop-p2（P2 通道与 worker）
真相源引用：MVP-ADAPTER-003（审核读面与 adapter 稳定性同族；新检查 ID 待规范复审后回填真相源）；AGENTS.md §1/§2/§4/§5/§5b
当前状态：doing（审核助攻实现的入仓；验收守门另立提案 review-contract-gate 待复核）
test_id：MVP-ADAPTER-003

| 项 | 内容 |
|---|---|
| 依赖 | P1 引擎与读面、P2 worker |
| test_id | `MVP-ADAPTER-003` |
| 允许修改 | `backend/app/assistant/tools.py`、`backend/app/assistant/prompts.py`、`backend/tests/test_assistant_loop.py`、本卡、认领、`docs/proposals/review-contract-gate.md`、`TASKS.md` |
| 禁止修改 | `backend/app/tasks/service.py`（守门提案落点，共享在途 FORMAL-MULTI-HARNESS-02）、`backend/app/admin/**`（共享）、`docs/spec/0.3-R10.md`、`docs/traceability.md`（不新增未登记 ID）、`backend/migrations/**`、生产项目素材与外部 Harness 工作区 |

## 做什么（P3 第一刀：审核助攻）

1. **`task.review_pack` 只读工具**（tools.py）：一次取证把审核要摆的东西
   摆齐——验收条款逐条（plan 快照里的 `{id, check, description}`）、
   diff 读数（含诚实 `available:false` 与原因）、最新验证回执；外加
   `check_kinds` 机械计数（没写 check 的条款被数出来，不被静默放过）。
   **不产出裁决**：approve/request_changes 属审核人。
2. **prompt 审核协议**：对照只述事实，条款能挂上证据就写挂上了，挂不上
   写"未记上"；裁决挂"待审核人"；不许把"测试绿"说成"人已查验"
   （human 类条款没有机械证据）。
3. **机械门不变**：引用 `[sN]/[cN]` 可回查、围栏、预算——审核助攻不新增
   引擎特权，还是"只读+说"。

## 为什么

审核人现在要自己把 plan 快照、diff、回执三处翻齐；"能读懂但写不成
规则"的对照是助手的活——但取证打包是 if/else，归平台代码先垫好。

## 修法

见上；实现细节在 `backend/app/assistant/tools.py` 的
`task_review_pack`（diff 键白名 passthrough、契约逐条取 `{id,check,description}`）。

## 完成标准（机械可判，全 scripted 不烧真额度）

1. `tests/test_assistant_loop.py`：审查包三读齐且路径/参数正确；条款与
   `check_kinds` 计数如实（含空 check 条款）；`include_patch` 只进 diff
   一读；diff 不可用与回执缺失诚实透传、不编；非标识符 task_id 拒收；
   loop 全链引用可回查；prompt 审核边界与自检第 7 项在案；工具白名单
   闭合（say 仍是唯一写）。
2. ruff（check+format 全仓）、`tools/docstate`、`tools/discipline` 绿；
   `pytest` 定点（assistant 三件套）绿，全量无新增失败。

## 状态

实现与测试已就位（本轮提交，sha 见认领）；守门半件按共享文件纪律另立
提案 `docs/proposals/review-contract-gate.md`，待仓库主人复核，不抢跑。
