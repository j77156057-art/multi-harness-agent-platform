# OFFICE-UI-02

### 统一任务卡

任务编号：OFFICE-UI-02
负责 Harness：Mavis
开始时间：2026-10-22 19:20 +08:00
工作区：`.worktrees/mavis/`
依赖任务：OFFICE-UI-01（办公室前端进仓）
真相源引用：R10 §8（attempt_budget_override）、§10（human.resume / human.reopen /
human.release / human.cancel / plan.reject 五条边）、§18（人类的五决策动词）、
§39（分配与传阅：裁决归管理员面）；AGENTS.md §1/§2/§4/§5/§5b
当前状态：doing
test_id：MVP-UI-012

| 项 | 内容 |
|---|---|
| 依赖 | OFFICE-UI-01 |
| test_id | `MVP-UI-012` |
| 允许修改 | `office/**`、`docs/claims/mavis--OFFICE-UI-02.md`、`docs/tasks/notes/OFFICE-UI-02.md`、`docs/traceability.md`、`TASKS.md` |
| 禁止修改 | `backend/**`、`docs/spec/**`、`backend/migrations/**`、其他 Harness 的工作区与分支 |

## 做什么

三件事，同一面：**让第一次用这个页面的人看得懂、点得对**。

1. 把「人自己的动作」补回办公室界面：**回答并恢复、放行、重新打开、取消这条活**，
   以及方案卡上的**驳回方案**。
2. 把界面的说法从平台内部名换成**人话**（状态、按钮、提示、窗口名、次数说法）。
3. 加一个独立窗口「各个窗口都说了什么」：按窗口摊开每条活最近一次的 CLI 原话。

## 为什么

### 一、按钮在迁移里丢了

旧的人操作面（`frontend/`，已随 `f1f9b7e` 删除）里有这些按钮：`MVP-UI-004`
明写"管理链与 §18 五决策动词接入三栏壳"，`frontend/src/components/TaskPane.vue`
里能读到 `RESUME_STATES` / `REOPEN_STATES` / `CANCEL_STATES` 与五个动作的分支。
迁到 Agent Office 时**整批只剩 API 与 CLI，界面上一个按钮都没有**。

后果不是"少几个按钮"，而是**状态成了死路**：

- `awaiting_human` 的任务在等一个人回答（R10 §10 `human.question`），
  而界面上除了发群消息没有别的出口 —— 平台不认群消息，任务就一直等；
- `escalated` 的任务没有放行出口（ADR-033 要求非终态有不依赖 owner 的恢复出口）；
- `abandoned` 的活人不能重开；
- 方案只能批准不能驳回 —— 批准成了"只能点头"。

后端端点一直都在（`/human/resume`、`/human/reopen`、`/human/release`、
`/human/cancel`、`/plans/{id}/reject`），CLI 也有对应命令
（`human-resume` / `human-reopen` / `human-release` / `human-cancel`）；
缺的只是界面。

### 二、字也没人看得懂

做法是**先看画面再改**：把办公室渲染出来（带假数据），逐屏截图，用眼睛看。
第一屏的问题不是「不好看」，是**读不懂**：

- 状态词是平台内部名：`待领取 / 待审 / 待人工 / 等人回答 / 方案漂移`；
- 按钮是平台动作名：`请求再给一次机会（预算豁免）` / `放行回队列` / `批准并派单`；
- 对话框里直接写 `决策行 / attempt / epoch / R10 §8`；
- 窗口显示成 id：`codex-window` 而不是「Codex 窗口」；
- 额度卡显示 `已用 undefined 次`（平台没给这个数时，界面把 undefined 印出来了）；
- 顶栏显示 `g · 已连接正式平台`（群 id）和一个孤零零的「你」。

走查还看出两个真缺陷：

- `hidden` 属性被 `display:grid` 盖掉 → 「顺便再给一次机会」那个勾选框
  **在每一个对话框里都露着**（静态测试全绿，肉眼才看得见）；
- 模态对话框是 `inset:0; margin:auto` 定位的，`height:auto` 会拉满视口 ——
  处置对话框虽有内容，却有 640px 高的一大片空白（要 `fit-content` 才按内容收）。

### 三、想看"他们说了什么"只能一条条点

平台记的是**结果**（谁接了、谁提交了、审没审过），人真正想看的是**窗口自己说的话**：
测试怎么报的错、改了哪些文件、最后写了什么。原来这些只在选中某条活的详情里看得到，
一条一条点。补一个独立窗口 `/outputs.html`（从办公室右栏「看他们说了什么」打开），
按窗口分组摊开。

**每一次尝试都摊开**，不只最近一次：一条活做过三次，这里就摊三次（新的在前），
同一条活的几轮收在一起，不把标题重复三遍。分组的键是**这一次尝试是谁做的**
（attempt 的 owner），不是活现在的主人 —— 改派过之后这两者不是一回事，而"谁当时
说了什么"才是这里要的。

数据一次取回：浏览器只发一个请求（`GET /api/formal/outputs`），网关在服务端并行取
每条活每一次尝试的 `/tasks/{id}/output?attempt_id=…`。attempt_id 是**算出来的**
（`{task_id}.a{attempt_no}`，见 `backend/app/claim/service.py`），所以不用先捞一遍
事件才知道有哪些尝试，直接按 1..attempt_no 枚举即可。

## 边界：什么**不**补，和为什么

按 R10 §39 与 `docs/tasks/notes/OFFICE-UI-01.md`（"派单、拆解、裁决、合并一律
由管理员 Harness 与成员自己走 API 完成，界面不代点"），下面这些**不进群主面**：

| 端点 | 不补的理由 |
|---|---|
| `POST /api/v1/adapter/admin/refine` | 需求细化是管理员面的活（R10 §22：群主只说目标，管理员细化并分派） |
| `POST /api/v1/groups/{id}/dispatch` | 派单归管理员 harness；网关的注释也明写"不得恢复人工派单" |
| `POST /api/v1/review/claim`、`/review/submit` | 审查归管理员与成员 |
| `POST /api/v1/human/adjudicate` | **裁决**归管理员面（R10 §39）。补它等于把裁决搬回群主面 |
| `POST /api/v1/projects` 等管理链 | 建项目/建群/发方案是管理员的动作；办公室大厅的"新建项目"按设计只写本机 localStorage |

代价要说清楚：**`escalated` 在群主面只有"放行"一条出口**，没有"受理升级 → 让人
回答"那条链（`human.adjudicate`）。要补那条链，得先有管理员面，那是另一张卡。

`human/adjudicate` 本身仍然够得着：网关有路由，CLI 有 `human-adjudicate`。

## 怎么做到不出错

1. **状态门控是抄后端的，不是凭感觉**：前端的状态集合在 `office/src/human-actions.js`，
   冒烟脚本从 `backend/app/human/service.py` **解析**出后端守卫再逐个比对，
   后端一改，前端这边立刻红。
2. **预算是显式问的**：R10 §8 规定预算用完的活普通的 release 会被拒绝，
   所以"放行"按钮在需要时明确问一次"顺便再给一次机会"，而不是让人从 409 里猜。
3. **不改后端**：这五个端点早就在，零后端改动、零 schema 改动、零事件名改动。
4. **说法改了不能把断言改松**：平台助手里那两个按钮（批准、改派）改了措辞，
   `verify-platform-assistant.mjs` 的断言同步改成新措辞 —— 断的是"这一节只该有
   人的批准按钮"这件事，不是"按钮必须长这样"。

## 完成标准

1. `office/scripts/verify-office-human-actions.mjs` 绿（16 项）：三个状态集合与
   后端守卫逐个相等、每个状态该有哪些按钮、预算豁免要显式问、
   网关把五个请求送到哪个后端端点并带对字段。
2. `office/scripts/verify-office-human-actions-ui.mjs` 绿（浏览器实测）：
   按钮真的渲染出来、点了真的打出 `/human/resume` 与 `/plan/reject`、
   页面不报错。该脚本自带静态服务器并拦截全部 `/api/formal/*`，不连在线平台。
   另外断言三条**走查才发现**的东西：回答的对话框里不出现预算勾选框、
   处置对话框高度按内容收着（< 400px）、预算用完的活放行前会问一次
   "再给一次机会"并带上 `budget_override`。
3. 界面上不出现平台内部名：`attempt` / `epoch` / `决策行` / `预算豁免` / `mention` /
   `stdout` 一律换成人话；窗口显示平台登记的名字而不是 id。
4. 「各个窗口都说了什么」独立窗口可用：按窗口分组（键是"这一次是谁做的"）、
   每一次尝试都摊开、显示原文、默认只显示做完了的、输出里的 HTML 被转义
   （CLI 说 `<b>x</b>` 时页面上是字面量）。
5. 既有冒烟全绿（`verify-office-task-view` / `brief-members` / `admin-chat` /
   `direct-chat` / `brief-live` / `layout` / `project-scope` / `platform-assistant` …）。
6. `office/` 构建通过（`npm run build`）。
7. `./scripts.sh check` 三道门禁全绿（docstate + discipline + ruff + pytest）。
8. `docs/traceability.md` 登记 `MVP-UI-012`（按钮）、`MVP-UI-013`（说法）、
   `MVP-UI-014`（独立窗口），状态如实写"部分有断言" —— 前端的静态检查
   **不在本仓门禁内**（CI 里没有 node），这一点不藏着。

## 验证结果

| 门禁 | 结果 |
|---|---|
| `office/scripts/verify-office-human-actions.mjs` | 16/16 PASS（纯 node，不依赖平台） |
| `office/scripts/verify-office-human-actions-ui.mjs` | ok（Chromium 实测，拦截 API） |
| `office/scripts/verify-office-read.mjs` | 34/34 PASS（既有） |
| `office/scripts/verify-routed-messages.mjs` | 5/5 PASS（既有） |
| `office/server/formal-gateway-admin.test.mjs` | 10/10 PASS（既有） |
| 其余既有冒烟 | 全 ok（对着带假数据的本地预览逐个跑过） |
| `office/` 构建 | `npm run build` 通过（161 kB JS / 48.7 kB CSS） |
| `./scripts.sh check` | 见提交记录 |
