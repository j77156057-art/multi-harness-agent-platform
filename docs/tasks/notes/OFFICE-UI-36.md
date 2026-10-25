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
当前状态：done
commit：`8734746`（助手的取证策略：事实本轮重新取，记忆只用来理解指代）
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
| 后端新增单测 | `backend/tests/test_assistant_evidence_policy.py` 新增 3 项（提示词里那条「本轮的事实本轮去取」在、提示词说明记忆是**上一轮**的读数、记忆行标身份）+ 记忆测试补 1 项（第三方的话不猜成用户说的）；助手全套全绿（**含 codex 的 `test_assistant_loop.py`**） |
| 真跑：同一道题问两遍（8849 真平台 + 4174 预览） | **第 1 遍**：「上一轮 [c7] 记下过 74 条及一组状态分布，但**那是上一轮的读数**，本轮没有重新取证，因此不能把它当作『现在』[c7]」→ **不再把过期数字当本轮结论**（此前它会把上一轮那句「在 FD-76… 处被截断」当今天的结论转述）。**第 2 遍**：它**自己取证了** → 「本项目共 74 条任务，其中 73 条还没人领，已被领走 1 条[s1]。依据：total=74，unclaimed.count=73，claimed.count=1[s1]」 |
| **没做完的一半（如实记下）** | **「主动去取证」仍是模型的选择**：第 1 遍它选择诚实地说「本轮未记上，建议重新取」，并没有自己去取。要让它**必须**取证，就得动引擎的机械门（`loop.py` —— codex 的在途范围），本张不做 |
| 办公室全部冒烟 | 32/35（3 条是与本卡无关的环境依赖：verify-anchors、verify-harness-connections、verify-project-workspace，与 OFFICE-UI-28~35 同） |
| `tools/docstate` / `tools/discipline` / `ruff` | 98/98、44/44；`ruff check` + `ruff format --check` 全绿 |
| `pytest`（全量） | EXIT=0 |
| 边界 | 引擎 `loop.py` 与工具表 `tools.py` 一行没改；`prompts.py` 只**加一条铁律**，不改 codex 写的任何一句 |
