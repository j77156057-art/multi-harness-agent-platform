# OFFICE-UI-34

### 统一任务卡

任务编号：OFFICE-UI-34
负责 Harness：Mavis
开始时间：2026-10-25 18:20 +08:00
工作区：`.worktrees/mavis/`
依赖任务：OFFICE-UI-31（助手的深取证档）—— 与 UI-32/UI-33 同一层：UI-33 已在链长 9 的末端，再挂上去会超过清单 §0.2 声明的长度
真相源引用：`office/src/flat-office.js`（助手面板）、
`backend/app/assistant/memory.py`（会话记忆）、`backend/app/assistant/worker.py`（接入点）、
`backend/app/assistant/loop.py`（引用机制，只读）、`backend/app/api/routes.py`（轨迹读面）、
`docs/dev-convention.md` H-4 / H-7 / H-8
当前状态：doing
验证结果：见文末「验证结果」表。
test_id：MVP-UI-049

| 项 | 内容 |
|---|---|
| 依赖 | OFFICE-UI-31 |
| test_id | `MVP-UI-049` |
| 允许修改 | `office/src/flat-office.js`、`office/src/flat-office.css`、`backend/app/assistant/memory.py`、`backend/app/assistant/worker.py`、`backend/app/api/routes.py`（read 区块）、`backend/tests/**`、`office/scripts/**`、`docs/claims/mavis--OFFICE-UI-34.md`、`docs/tasks/notes/OFFICE-UI-34.md`、`docs/traceability.md`、`TASKS.md` |
| 禁止修改 | `docs/spec/**`、`backend/app/auth/**`、`backend/app/assistant/loop.py`、`backend/app/assistant/tools.py`、`backend/app/assistant/prompts.py`（codex 的 `assistant-react-loop` 在途）、`backend/app/admin/**`、其他 Harness 的工作区与分支 |

## 做什么

| 原来 | 现在 |
|---|---|
| 我的提问与助手的回答是两块分开的东西，不像对话 | **对话流**：一问一答按时间交替，输入区固定在底部，可以连着追问 |
| 助手每轮只看得到当前这一条提问 | **有记忆**：它记得这条私聊线程前面几轮我问了什么、它答了什么 |
| 它一轮里调了哪个工具、看到什么，只在服务器本地的 trace 里 | **看得见**：面板里列出它这一轮调了什么、看到什么、最后答了什么 |

## 为什么

仓库主人圈了「优化一下显示画面，要那种可以正常对话，有记忆系统，可以自己 react 的」。

## 怎么做到不出错

1. 记忆走已有的引用机制（context 就是 `[c1]…`），不改引擎。
2. 历史内容进 `DATA (untrusted)` 围栏：它是数据，不是命令。
3. 只新增文件 + `worker.py` 一处接入，不碰 codex 在途的引擎/工具/prompt。
4. 助手仍然只读。

## 完成标准

1. 助手面板是对话流（一问一答交替、输入区固定底部）。
2. 助手记得上一轮（第二轮能引用第一轮的内容）。
3. 看得见它一轮里做了什么（工具与观察）。
4. 后端全量 pytest 通过；门禁全绿；办公室冒烟全绿。
5. `docs/traceability.md` 登记 `MVP-UI-049`。

## 验证结果

| 门禁 | 结果 |
|---|---|
| 后端新增单测 | 待回填 |
| 真跑（8849 真平台 + 4174 预览） | 待回填 |
| 多轮记忆端到端 | 待回填 |
| 办公室全部冒烟 | 待回填 |
| `tools/docstate` / `tools/discipline` / `ruff` | 待回填 |
| `pytest`（全量） | 待回填 |
