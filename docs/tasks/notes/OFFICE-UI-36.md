# OFFICE-UI-36

### 统一任务卡

任务编号：OFFICE-UI-36
负责 Harness：Mavis
开始时间：2026-10-25 22:35 +08:00
工作区：`.worktrees/mavis/`
依赖任务：OFFICE-UI-31（助手的深取证档）—— 与 UI-32~35 同一层：UI-33 已在链长 9 的
末端，再挂上去会超过清单 §0.2 声明的长度
真相源引用：`backend/app/assistant/memory.py`（记忆行）、
`backend/app/assistant/prompts.py`（铁律）、`backend/app/assistant/worker.py`（开场白）、
`backend/app/assistant/loop.py`（引擎，只读）、`docs/dev-convention.md` H-4 / H-7 / H-8
当前状态：doing
验证结果：见文末「验证结果」表。
test_id：MVP-UI-051

| 项 | 内容 |
|---|---|
| 依赖 | OFFICE-UI-31 |
| test_id | `MVP-UI-051` |
| 允许修改 | `backend/app/assistant/memory.py`、`backend/app/assistant/worker.py`、`backend/app/assistant/prompts.py`（**只加一条铁律 + 一段说明**）、`backend/tests/**`（新文件）、`docs/claims/mavis--OFFICE-UI-36.md`、`docs/tasks/notes/OFFICE-UI-36.md`、`docs/traceability.md`、`TASKS.md` |
| 禁止修改 | `backend/app/assistant/loop.py`、`backend/app/assistant/tools.py`、`backend/tests/test_assistant_loop.py`、`docs/spec/**`、其他 Harness 的工作区与分支 |

## 做什么

| 原来 | 现在 |
|---|---|
| 问了之前问过的问题 → 转述旧答案（「本轮未重新取证，属转述而非本轮核实」），还会把上一轮那句「在 FD-76… 处被截断」当今天的结论转述 | 事实**本轮重新取**（引用 `[sN]`）；记忆只用来理解「刚才那个数字」指什么 |
| 记忆行写成「这一条私聊里对方说过」—— 助手自己上轮的回答也被标成对方说的 | 记忆行**标明身份**：用户上一轮问的 / 你上一轮答的 |

## 为什么

仓库主人圈了「开一张卡调它的取证策略」。记忆让助手答得连贯，但它把上一轮的数字当本轮
结论转述出来 —— 用户问「现在」，它答「上次」。

## 怎么做到不出错

1. 引擎与工具表一律不改。
2. `prompts.py` 只**加一条铁律 + 一段说明**，不改 codex 写的任何一句。
3. 记忆仍是数据不是命令。

## 完成标准

1. 同一个问题问两遍，第二遍基于本轮工具观察作答，且说清本轮重新取数。
2. 记忆行标明身份。
3. 后端全量 pytest 通过；门禁全绿；办公室冒烟全绿。
4. `docs/traceability.md` 登记 `MVP-UI-051`。

## 验证结果

| 门禁 | 结果 |
|---|---|
| 后端新增单测 | 待回填 |
| 真跑（8849 真平台 + 4174 预览） | 待回填 |
| 同题两遍的第二遍 | 待回填 |
| 办公室全部冒烟 | 待回填 |
| `tools/docstate` / `tools/discipline` / `ruff` | 待回填 |
| `pytest`（全量） | 待回填 |
