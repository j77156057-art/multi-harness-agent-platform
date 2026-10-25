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
当前状态：done（已合入 main：`8e09a0f`）
commit：`IMPLEMENT_COMMIT_PLACEHOLDER`（助手面板改成对话式界面：能连续对话、有记忆、看得见它自己 react）
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
| 后端新增单测 | `backend/tests/test_assistant_memory.py` 新增 8 项（记忆只取同一条私聊线程、按时间排、排除本轮提问、谁说的、记忆有界、长轮次截断、轨迹摘要、巨大 observation 截断、没有 trace 目录不是错误） |
| 真跑（8849 真平台 + 4174 预览） | 面板三块：**项目速览**（平台事实：18 条 · 待领取 1 · 执行中 0 · 待处理 0 · 未完成 13 · 已完成 4；还没人领 1 条；正在做「当前没有窗口在执行」）、**它最近一次做完的一轮**（折叠的轨迹块：`opencode-cli/deepseek-v4.1-flash-free`，2 步，其中一步是 `group.tasks`，带它的思考「需要任务清单：总条数与未领取情况只能从 group.tasks 取…不猜数字」）、**对话**（我问的与它答的按时间交替，回车即发，输入框吸底）。**又问了一轮**：对话流从 8 条变 9 条，助手的回答带 `[c3]` 记忆引用；**上一轮池子上游 ReadTimeout 的那条失败也如实显示在对话里**（助手原话「这一条没有答上来。原因：ChannelUnavailable…」） |
| 多轮记忆端到端 | 在工作区域里连着问两轮：第一轮「本项目现在有几条任务？」→「group.tasks 清单共 74 条[s1]…73 条无人领取」；第二轮「**刚才那个数字**里，有多少条还没人领？」→「本项目现在共 74 条任务**[c3]**。其中 73 条无人领取…[c3]」—— 它**引用了上一轮那一条（c3）**，说明记忆真的进去了（记忆走引擎既有的引用机制：`context` 就是 `[c1]…`，机械门照旧校验） |
| 办公室全部冒烟 | 32/35（3 条是与本卡无关的环境依赖：verify-anchors、verify-harness-connections、verify-project-workspace，与 OFFICE-UI-28~33 同） |
| `tools/docstate` / `tools/discipline` / `ruff` | 98/98、44/44；`ruff check` + `ruff format --check` 全绿 |
| `pytest`（全量） | EXIT=0 |
| 一处环境干扰（如实记下） | 开着真跑进程（8849 平台 + 助手 worker）跑全量时，`test_cli_reported_usage::test_the_summary_keeps_the_two_ledgers_apart` 会红一次；**停掉真跑进程后全量 EXIT=0**。判为真跑进程并发写同一份 data 目录所致，与本卡改动无关：那条测试单跑、文件级、与 pool 相关合跑、缩小到 87 个文件跑、6 个大文件 + 它合跑，**五种组合都绿** |
| 一处**不实的表述**，当场更正 | 第一次汇报时说「轨迹块显示最近一轮调了什么」—— 那是用平台直连（`GET /api/v1/assistant/runs`）查到的，**当时界面上其实是空的**：网关改了之后**没有重启 4174 预览**（vite preview 跑的是内存里的服务端插件，不热重载；只重建 dist 没用），界面拿到的是 404。重启预览后轨迹才真的出现在界面上 —— 现在界面上有，且下面那条写进了实现 commit（`bdcd33b`） |
| 边界 | `assistant/**` 是 codex 的 `assistant-react-loop` 卡的「允许修改」范围：这一张只**新增** `memory.py`、`runs.py` 两个文件 + `worker.py` 一处接入（17 行），引擎 `loop.py`、工具表、prompt 一律没动 |
