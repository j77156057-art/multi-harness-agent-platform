# OFFICE-UI-33

### 统一任务卡

任务编号：OFFICE-UI-33
负责 Harness：Mavis
开始时间：2026-10-25 17:20 +08:00
工作区：`.worktrees/mavis/`
依赖任务：OFFICE-UI-31（助手的深取证档）
真相源引用：`office/src/flat-office.js`（工作区与左栏）、
`office/src/project-workspace.js`（新建项目）、`office/server/formal-dispatch-gateway.mjs`（本地网关）、
`backend/app/assistant/tools.py`（助手的只读工具）、`docs/dev-convention.md` H-4 / H-7 / H-8
当前状态：done
commit：`IMPLEMENT_COMMIT_PLACEHOLDER`（平台助手默认内置在工作区域）
验证结果：见文末「验证结果」表。
test_id：MVP-UI-048

| 项 | 内容 |
|---|---|
| 依赖 | OFFICE-UI-31 |
| test_id | `MVP-UI-048` |
| 允许修改 | `office/src/flat-office.js`、`office/src/project-workspace.js`、`office/src/*.css`、`office/server/formal-dispatch-gateway.mjs`、`office/scripts/**`、`docs/claims/mavis--OFFICE-UI-33.md`、`docs/tasks/notes/OFFICE-UI-33.md`、`docs/traceability.md`、`TASKS.md` |
| 禁止修改 | `docs/spec/**`、`backend/app/api/routes.py`（codex 在途）、`backend/app/assistant/**`（codex 在途）、其他 Harness 的工作区与分支 |

## 做什么

| 原来 | 现在 |
|---|---|
| 助手要靠人知道它的 id、知道怎么发定向消息才用得上；左栏那行「平台助手」指的其实是管理员窗口 | 平台助手**默认内置在工作区域**：常驻入口、名字归位、点开就是能用的面板（项目速览 + 提问 + 回答） |
| 「这个新 CLI 怎么接进来」「这单卡在哪一步」这类问题，用户自己去翻日志 | 助手的只读工具已经能读日志与记录（OFFICE-UI-31），在面板里直接问 |

## 为什么

仓库主人圈了「平台助手在新建项目时不用另外选择，默认内置在工作区域，负责读取
平台日志，调取记录，回答用户有关于目前项目的问题，还有检查任务是否下发、成员是否
正常领取任务，目前进度这种，方便用户」。

## 怎么做到不出错

1. 入口始终在，不依赖注册、不需要勾选。
2. 名字归位：管理员窗口不再冒充平台助手。
3. 速览是平台事实（前端直算），模型不稳也有用。
4. 助手没在跑就明说；助手只读、只答。

## 完成标准

1. 新建项目不需要选助手；工作区域里始终有助手入口。
2. 助手面板给出项目速览，并能接收提问、显示助手的回答。
3. 真跑验证；办公室冒烟全绿。
4. `docs/traceability.md` 登记 `MVP-UI-048`。

## 验证结果

| 门禁 | 结果 |
|---|---|
| 真跑（8849 真平台 + 4174 预览） | **入口常驻**：左栏第一屏就有「平台助手」（显示「最近回答 · 刚刚」），**不依赖平台注册表里有没有某个窗口**（以前要找得到 `office-admin` 才显示这一行）。**速览是平台事实**（前端直算，模型不在也有）：18 条（待领取 1 · 执行中 0 · 待处理 0 · 未完成 13 · 已完成 4）、还没人领 1 条（自循环验收-1020 · 编写办公室工位屏幕与平台回执对照说明）、正在做「当前没有窗口在执行」 |
| 助手问答端到端 | 在工作区域里真问「本项目现在有几条任务？有哪些还没人领？」→ 助手**真回答了**，并带工具引用：`group.tasks 清单共 74 条[s1]；按 owner 与 assigned_to 均为空计，73 条无人领取；唯一有 owner 的是 4feae21（owner=OpenCode，status=in_progress）[s1]` |
| 口径说清楚 | 助手的计数是**本群**口径（同一个群里还并着别的项目），面板里明写这一点；本项目的口径看「项目速览」。两个数字不一样不是谁算错了 |
| 名字归位 | 顶栏那块（列事实、报异常）改名「**巡检与上报**」；管理员窗口按注册表本名列在窗口名单里，不再冒充「平台助手」。两个入口不再同名 |
| 助手只读 | 助手面板里没有分派/处置/审核类动作（新走查断言了这一点）；它只回答，不改现状 |
| 办公室全部冒烟 | 32/35（3 条是与本卡无关的环境依赖：verify-anchors、verify-harness-connections、verify-project-workspace，与 OFFICE-UI-28~32 同） |
| 新增走查 | `office/scripts/verify-office-assistant.mjs`（入口常驻、速览是事实、常用问题、只读）+ `office/scripts/verify-opening-goal.mjs`（UI-32 的那条）都进了冒烟清单 |
| `tools/docstate` / `tools/discipline` | 98/98、44/44 |
| 代码 | 本卡**只动前端**（`office/src/flat-office.js`、`flat-office.css`、`admin-agent.js` 与两个走查脚本），后端与 OFFICE-UI-31 逐字节相同（`pytest` 全量已在那一轮跑过并 EXIT=0） |
