# OFFICE-UI-22

### 统一任务卡

任务编号：OFFICE-UI-22
负责 Harness：Mavis
开始时间：2026-10-25 00:45 +08:00
工作区：`.worktrees/mavis/`
依赖任务：OFFICE-UI-12（会话列表）、OFFICE-UI-21（成员消息那条路）
真相源引用：`office/src/main.js`（顶栏）、`office/src/flat-office.js`（窗口页与会话
列表）、`office/src/project-workspace.js`（大厅的窗口名单）、
`backend/app/admin/worker.py`（管理员的消息处理）、`backend/app/tasks/service.py`
（消息签名）；R10 §19（群聊与消息）、§38（R10-fix 流程）；
`docs/dev-convention.md` H-4 / H-7 / H-8
当前状态：done
commit：`5ac3533`（状态栏的在线/离线、中途把新窗口加进项目、加完通知管理员；R10 §38.17 R10-fix-23 + §19 一条）
验证结果：见文末「验证结果」表。
test_id：MVP-UI-037

| 项 | 内容 |
|---|---|
| 依赖 | OFFICE-UI-12、OFFICE-UI-21 |
| test_id | `MVP-UI-037` |
| 允许修改 | `backend/app/tasks/service.py`、`backend/app/admin/worker.py`、`backend/tests/test_admin_worker.py`、`office/src/main.js`、`office/src/flat-office.js`、`office/src/flat-office.css`、`office/src/project-workspace.js`、`office/scripts/verify-office-conversations.mjs`、`docs/claims/mavis--OFFICE-UI-22.md`、`docs/tasks/notes/OFFICE-UI-22.md`、`docs/traceability.md`、`docs/spec/0.3-R10.md`（§38.17 R10-fix-23 + §19）、`TASKS.md` |
| 禁止修改 | `backend/app/api/routes.py`、`backend/app/pool/**`、`backend/app/tasks/service.py` 以外的任务状态机、`docs/dev-convention.md`、`tools/**`、其他 Harness 的工作区与分支 |

## 做什么

| 要什么 | 怎么做 |
|---|---|
| 丰富状态栏 | 顶栏：「已连接平台 · 窗口 7 在线 · 2 离线」，**跟着每轮刷新变**；平台读不到时说「未连接」 |
| 增加离线/在线状态 | 窗口的在线/离线取平台 `/adapter/availability` 的 `status`（不猜）；大厅的窗口名单不再把在线写死 |
| 中途加入新的 harness | 窗口页上，没在这个项目里的窗口给一个「加入这个项目」；加入即写进项目成员 |
| 加入以后通知管理员 | 加入时发一条带 `add-member` 签名的群消息；管理员知道、在群里说一声、并把**还在等人领的活**改派给它 |

## 为什么

仓库主人：「丰富一下状态栏，增加离线，在线状态，增加中途加入新的harness功能，加入以后
通知管理员」。

## 怎么做到不出错

1. 在线/离线是平台上的事实（`/adapter/availability` 的 `status`）✓。
2. 加人不改已派出去的活：只接手还在等人领的活 ✓。
3. 加完只通知一次 ✓。
4. 状态栏不撒谎：读不到就说未连接 ✓。

## 完成标准

1. 顶栏给出在线/离线数，并随刷新变；平台断开时说「未连接」。
2. 大厅的窗口名单用真实状态（离线的窗口标出来）。
3. 窗口页能「加入这个项目」，加入后项目成员里有它。
4. 加入时通知管理员；管理员收到后知道、在群里说一声、并把等人领的活改派给它。
5. 断言通过；办公室冒烟无回归；构建通过；`./scripts.sh check` 全绿。
6. `docs/traceability.md` 登记 `MVP-UI-037`；R10 §38.17 R10-fix-23 与实现同一次提交。

## 验证结果

| 门禁 | 结果 |
|---|---|
| `backend/tests/test_admin_worker.py` | ok（新增 1 条：管理员收到「中途加入」的消息 → 知道、在群里说一声、把等人领的活改派给新窗口、只处理一次） |
| `office/scripts/verify-office-conversations.mjs` | ok（状态栏给出在线数、窗口页有「加入这个项目」、加入后确实通知了管理员） |
| 真跑（8849 真平台 + 4174 预览） | 状态栏「已连接平台 · 窗口 12 在线」；左栏列出 12 个窗口；窗口页有「加入这个项目」，点了之后界面显示「已通知管理员」，平台群里收到那条带 `add-member` 的消息；页面无报错 |
| 办公室全部冒烟 | ok（30/33；3 条恒失败是环境依赖：Godot dev server、本机 ollama `qwen3:4b`、在册凭据窗口 7≠9） |
| `office/` 构建 | ok（29 个模块，main 210.67 kB JS） |
| `tools/docstate` / `tools/discipline` / `ruff` | 98/98、44/44、ruff 全绿 |
| `pytest`（全量） | `PYTEST_EXIT=0` |
