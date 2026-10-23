# OFFICE-UI-08

### 统一任务卡

任务编号：OFFICE-UI-08
负责 Harness：Mavis
开始时间：2026-10-23 22:40 +08:00
工作区：`.worktrees/mavis/`
依赖任务：OFFICE-UI-02（办公室界面）、OFFICE-UI-04（平台翻译）
真相源引用：R10 §19（群聊和上下文 —— 群聊是平台上的事实，不是本地的假消息）；
§15（事件、事实源）；`backend/app/wording/service.py`（机器文本的人话表）；
`docs/dev-convention.md` H-4 / H-7 / H-8
当前状态：doing
test_id：MVP-UI-023

| 项 | 内容 |
|---|---|
| 依赖 | OFFICE-UI-02、OFFICE-UI-04 |
| test_id | `MVP-UI-023` |
| 允许修改 | `office/src/flat-office.js`、`office/src/flat-office.css`、`office/src/wording.js`、`office/src/main.js`（办公室自己发的文案）、`office/scripts/verify-office-*.mjs`、`backend/app/wording/**`、`backend/tests/test_wording.py`、`docs/claims/mavis--OFFICE-UI-08.md`、`docs/tasks/notes/OFFICE-UI-08.md`、`docs/traceability.md`、`TASKS.md` |
| 禁止修改 | `backend/app/api/routes.py`、`backend/app/tasks/**`、`backend/app/claim/**`、`backend/app/pool/**`、`docs/spec/**`、`backend/migrations/**`、其他 Harness 的工作区与分支 |

## 做什么

群聊顶上给一段**「最近群里在说什么」**：多少条、谁说的、今天有没有新动静、最近一条是什么。

## 为什么

群聊是办公室里唯一"只能自己读"的地方 —— 任务列表能分组、能筛选、能摘要（头顶那段
「今天」），群聊什么都不能。180 条消息里往往只有最近几条是要紧的，但**哪几条要紧**
人一眼看不出来。

真数据里 180 条的构成：管理员窗口 121 条、人 55 条、成员 4 条 —— 大多是平台的回执，
不是聊天。人要的就是"谁说了多少、最近那条是什么"。

## 怎么做到不出错

1. **每句都是算出来的**：条数与分工按 `sender_type` 和平台名册算；今天有没有新动静按
   **本机时区**的今天算（跟顶上那段「今天」一个口径）。
2. **机器标记不直接摆**：消息正文先让平台洗掉机器标记（`kind=text`），再把**任务号**
   换成任务名、**窗口 id**换成登记名 —— 人读到的是"哪条活、哪个窗口"。
3. **接替关系不摆成"甲 → 甲"**：改派那条消息里前后两个编号常常是同一条活的两代，
   换出来的名字一样，就只留一个。
4. **窗口 id 长的先换**：`codex-window-admin` 与 `codex` 都登记着，先换短的会把长的
   截成"Codex CLI-window-admin"；前后还要有边界（免得把 "pipe" 里的 pi 也换了）。

## 完成标准

1. 群聊顶上有一段摘要：一共多少、谁说的、今天有没有新消息、最近一条是什么。
2. 消息正文与摘要里的任务号 / 窗口 id 都换成人读的名字。
3. 新增断言钉住以上几条；办公室冒烟全绿；`office/` 构建通过；`./scripts.sh check` 全绿。
4. `docs/traceability.md` 登记 `MVP-UI-023`。

## 验证结果

| 门禁 | 结果 |
|---|---|
| `office/scripts/verify-office-chat-digest.mjs`（新增） | ok |
| `backend/tests/test_wording.py` | 全绿（加了"管理员 Harness → 管理员窗口"一条） |
| 办公室冒烟 | 见下 |
| `tools/docstate` / `tools/discipline` / `ruff` | 全过 |
| 真跑 | 8850 真数据副本（180 条群聊） |

真数据上的摘要：

> 群里 180 条消息：Codex · 管理员 121 条、你 55 条、其他成员 4 条。今天没有新消息 ——
> 最后一条是 1 天前。管理员窗口改派：「建立工位屏幕与回执字段映射」（管理员把这条活分给
> 更闲的窗口：原执行者 → OpenCode）。

展开后的消息也一样：说话人是登记名（不是 `codex-window-admin`），成员名单是
「Claude Code、CodeBuddy · 成员1、Codex CLI、OpenCode、Pi CLI、Qoder · 成员1、
Qoder · 成员2」。

## 顺带清掉的一处

办公室自己发的项目目标消息里写着"管理员 Agent 将据此拆解任务" —— 那是**管理员窗口**，
不是"Agent"。改掉（`office/src/main.js`）。
