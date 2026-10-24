# assistant-diagnose-pack

### 统一登记

任务编号：assistant-diagnose-pack
执行 Harness：Codex
开始时间：2026-10-23 19:36 +08:00（仓库主人指示"继续完善 agent 搭建"；
守门因 tasks/service.py 共享在途未收敛，按 roadmap 先行证据束取样器）
工作区：.worktrees/codex/
依赖任务：assistant-react-loop-p3（引擎与审核助攻已就位）
真相源引用：R10 §19.2（额度池语义）、§10（事件账本语义）；AGENTS.md
§1/§2/§4/§5/§5b；`docs/proposals/assistant-react-loop.md`（P2/P3 路线图
"证据束取样器"条目）
当前状态：doing（取证束打包＋诊断姿势 prompt＋scripted 测试）
test_id：`MVP-ADAPTER-003`

| 项 | 值 |
|---|---|
| 依赖 | assistant-react-loop-p3 |
| test_id | `MVP-ADAPTER-003` |
| 允许修改 | `backend/app/assistant/tools.py`、`backend/app/assistant/prompts.py`、`backend/tests/test_assistant_loop.py`、`docs/tasks/notes/assistant-diagnose-pack.md`、`docs/claims/codex--assistant-diagnose-pack.md`、`TASKS.md` |
| 禁止修改 | `backend/app/tasks/**`（守门落点，共享在途）、`backend/app/api/**`（R10-fix-13 在途）、`backend/app/admin/**`、`backend/app/auth/**`、`backend/migrations/**`、`docs/spec/**`、`docs/traceability.md`（不新增 ID） |

## 是什么

有人问"这单为什么失败"时，助手不能再靠翻账本念经。`task.diagnose`
只读工具一趟把失败证据束摆齐：

- 任务状态/标题/owner/current_attempt；
- `/events` 里 abandon/release 类失败事件（最新至多 3 条，payload 原样）；
- `/output` 的 CLI stdout/stderr 尾（拿不到如实 `None`）；
- 验证回执（成功与否都带着）。

只组读面已公开的事实。"这批证据说明根因是什么"的**人话根因**是助手
的活（prompt 协议），打包是 if/else（平台代码）。

## 为什么

路线图（`docs/proposals/assistant-react-loop.md`）P2/P3 线里登记过
"证据束取样器（Diagnostic：消费 failure.json → 人话根因）"，一直没
落地。落点受共享在途约束：结构化 failure.json 在磁盘、`api/routes.py`
被 R10-fix-13 占用——所以先以"读面拼盘"落地，不开新路由、不开新面。

## 修法

见上；实现细节在 `backend/app/assistant/tools.py` 的
`task_diagnose`（事件过滤＝机械计数，最新至多 3 条；四读一次取准），
诊断姿势进 `prompts.py`。

## 完成标准（机械可判，全 scripted 不烧真额度）

1. `tests/test_assistant_loop.py`：诊断束四读齐且路径正确；失败事件
   过滤正确且"最新至多 3 条"；output/verification 缺失诚实透传，不编；
   非标识符 task_id 拒收；白名单闭合（含 task.diagnose）＋say 唯一写；
   诊断姿势与自检条目在案；
2. ruff（check+format 全仓）、`tools/docstate`、`tools/discipline` 绿；
   `pytest` 定点（assistant）绿，全量无新增失败。

### 验收结果

（完成时回填）
