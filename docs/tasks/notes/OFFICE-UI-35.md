# OFFICE-UI-35

### 统一任务卡

任务编号：OFFICE-UI-35
负责 Harness：Mavis
开始时间：2026-10-25 21:35 +08:00
工作区：`.worktrees/mavis/`
依赖任务：OFFICE-UI-31（助手的深取证档）—— 与 UI-32/33/34 同一层：UI-33 已在链长 9
的末端，再挂上去会超过清单 §0.2 声明的长度
真相源引用：`backend/app/assistant/tools.py`（工具的返回形状）、
`backend/app/assistant/loop.py`（引擎，只读）、`backend/app/assistant/prompts.py`（姿势，
只读）、`docs/dev-convention.md` H-4 / H-7 / H-8
当前状态：doing
验证结果：见文末「验证结果」表。
test_id：MVP-UI-050

| 项 | 内容 |
|---|---|
| 依赖 | OFFICE-UI-31 |
| test_id | `MVP-UI-050` |
| 允许修改 | `backend/app/assistant/tools.py`（**只改 `group_tasks` 一个函数**）、`backend/tests/**`（新文件）、`docs/claims/mavis--OFFICE-UI-35.md`、`docs/tasks/notes/OFFICE-UI-35.md`、`docs/traceability.md`、`TASKS.md` |
| 禁止修改 | `backend/app/assistant/loop.py`、`backend/app/assistant/prompts.py`、`backend/app/assistant/worker.py`、`backend/app/assistant/memory.py`、`backend/app/assistant/runs.py`、`backend/tests/test_assistant_loop.py`（codex 在途）、`docs/spec/**`、其他 Harness 的工作区与分支 |

## 做什么

| 原来 | 现在 |
|---|---|
| `group.tasks` 把本群几十条任务的**全字段**整份塞进一次观察 → 模型只看到前一半 → 回答里出现「在 FD-76… 处被截断」 | 一次看到**完整摘要**：总数、按状态分布、没被领的与已有人领的清单（少量字段）、**明确省略了多少条**；要全量可以 `limit` / `offset` 翻页 |

## 为什么

仓库主人指出「怎么回答后面都有省略号，回答完了吗」—— 查下来平台里的原文是完整的，被切
的是它看到的那份工具观察。助手没有编（它明说「未记上，也不补编」），但体验上就是
「没答完」。

## 怎么做到不出错

1. 只改一个函数的返回形状：引擎、提示词、工具名单一律不动。
2. 摘要里说清省略了多少条、怎么翻页（别把「没列全」变成另一种看不见的截断）。
3. 助手仍然只读。

## 完成标准

1. 回答里不再出现半截清单；要么完整摘要，要么明说「共 N 条、列了 M 条、要全量请翻页」。
2. `group.tasks` 支持 `limit` / `offset`。
3. 后端全量 pytest 通过；门禁全绿；办公室冒烟全绿。
4. `docs/traceability.md` 登记 `MVP-UI-050`。

## 验证结果

| 门禁 | 结果 |
|---|---|
| 后端新增单测 | 待回填 |
| 真跑（8849 真平台 + 4174 预览） | 待回填 |
| 办公室全部冒烟 | 待回填 |
| `tools/docstate` / `tools/discipline` / `ruff` | 待回填 |
| `pytest`（全量） | 待回填 |
