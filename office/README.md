# Agent Office —— 办公室（数据版）

平台的**观察面**：看任务、方案、额度、事件、群聊，以及跟管理员对话。
它不派单、不改派、不审核、不合并 —— 那些是管理员 Harness 与成员自己的动作，
界面只提供**人自己的动作**（请求改派、请求预算豁免、请管理员派单、在群里说话，
以及 R10 §18 的几个处置动词：回答并恢复、放行、重新打开、取消这条活，
外加方案上的批准与驳回）。

裁决（`human.adjudicate`）、派单、细化、审查、管理链**不在群主面** ——
那是管理员面的事（R10 §39、`docs/tasks/notes/OFFICE-UI-01.md`）。

## 连上真平台（第一次跑必看）

`npm run dev` / `npm run preview` 起的是**界面**，默认读不到任何任务 —— 界面会
在顶上写「未连接正式平台」。要看到真的东西，**先把这几行环境变量设上**：

```powershell
# 正式平台跑在 8848；办公室自己跑在 4174
$env:OFFICE_FORMAL_UPSTREAM   = 'http://127.0.0.1:8848'
# 令牌文件：路径，不是令牌本身（令牌别复制进仓库）
$env:OFFICE_FORMAL_TOKEN_FILE     = 'D:/study/tools/agent/new agent/data/formal-operator.key'
$env:OFFICE_FORMAL_ADMIN_TOKEN_FILE = 'D:/study/tools/agent/new agent/data/codex-window-admin.key'
# 这个预览服务负责哪一段
$env:OFFICE_FORMAL_PROJECT = '112'
$env:OFFICE_FORMAL_GROUP   = 'office-formal-2'
```

然后：

```powershell
npm ci
npm run build
npm run preview        # http://127.0.0.1:4174
```

- **令牌文件在哪**：`data/` 目录下的 `*.key`，一个文件一个身份。
  `formal-operator.key` 是**人**（操作员），`codex-window-admin.key` 是**管理员窗口**。
- **项目号/群号从哪来**：平台那边 `GET /api/v1/groups?project_id=<项目号>` 会列出
  群；`office/.env.example` 里那组是本机现成的一套。
- **忘了设会看到什么**：界面照常打开，但顶上写「未连接正式平台」，任务、方案、
  额度全是空的 —— 不是坏了，是没接上。

```
npm ci
npm run build          # 构建到 dist/
npm run dev            # 本地开发（走 server/ 里的网关）
npm run preview        # 预览构建产物（4174）
npm run verify         # 跑 scripts/verify-*.mjs 全部冒烟脚本
```

## 目录

| 路径 | 是什么 |
|---|---|
| `src/main.js` | 应用外壳：顶栏、项目大厅、办公室、开工配置对话框 |
| `src/flat-office.*` | 办公室本体：任务表、任务详情、额度池、群聊、任务书、成员动态 |
| `src/outputs.*` | **独立窗口**：各个窗口做完活之后都说了什么（按窗口分组） |
| `src/human-actions.js` | 「人自己的动作」的状态门控（抄后端守卫，冒烟脚本会逐个核对） |
| `src/project-workspace.*` | 项目大厅（项目列表、新建项目、连接 Harness） |
| `src/opening-host.js` | 开工向导的宿主（群主填目标，管理员 Harness 细化并分派） |
| `src/admin-agent.js` | 平台助手：巡检与上报，**只读**，不代任何人做决定 |
| `server/` | 预览层网关：把浏览器的 `/api/formal/*` 转到正式平台，并按群裁剪 |
| `contributions/office-opening-phone/` | 哈希锁定的组件，**不要改**（改动走它自己的流程） |
| `scripts/verify-*.mjs` | 冒烟脚本（见下） |

## 读数据的方式

界面只读平台的正式接口，不自造事实：

- `/api/formal/status` —— 项目、群、管理员、窗口名册；
- `/api/formal/tasks` —— 本项目的任务；
- `/api/formal/events` —— **本群**的事件流（成员动态、任务书里的"谁接过"都从这来）；
- `/api/formal/messages` —— 群消息（`mentions` 是平台的投递机制）；
- `/api/formal/plan` —— 方案定稿（任务书的原文）。

## 人自己的动作

这些按钮**按后端的状态守卫给出**（`src/human-actions.js` 与
`backend/app/human/service.py` 一一对应；后端仍是唯一裁判，409 照实显示）：

| 动作 | 网关路由 | 平台端点 |
|---|---|---|
| 回答并恢复 | `POST /api/formal/human/resume` | `POST /api/v1/human/resume` |
| 重新打开 | `POST /api/formal/human/reopen` | `POST /api/v1/human/reopen` |
| 放行回队列 | `POST /api/formal/human/release` | `POST /api/v1/human/release` |
| 取消这条活 | `POST /api/formal/human/cancel` | `POST /api/v1/human/cancel` |
| 驳回方案 | `POST /api/formal/plan/reject` | `POST /api/v1/plans/{id}/reject` |

预算用完的活放行时要显式带上**预算豁免**（R10 §8），所以对话框里会多问一次，
而不是让人从 409 里猜。

## 自己的 API key（额度池里）

额度池卡片里有一块「自己的 API key」：加一个你自己的 provider key（名字、协议、
地址、模型、key 本体）。平台替人保管它 —— **key 永不出接口**（列表上只有
`sk-…abcd` 这样的遮罩）。

它排在免费档、计量档**之后**，而且平台**不会自己花**：免费与计量都拿不出额度时，
平台**来问**你 —— 右栏最上面出现一张「平台在问你一件事」的卡（三个按钮：先不用 /
同意用一次 / 以后都用它）。点了头，池子才会用那一档；「用一次」被那一次请求用掉
就失效。

没点头时，池子给 worker 的拒绝是 **409 `pool_user_key_pending`**（不是 500）：
worker 分得清"没这个模型"和"用户在等点头"，不会一直重试。

| 动作 | 网关路由 | 平台端点 |
|---|---|---|
| 列出（含未答的问题） | `GET /api/formal/user-keys` | `GET /api/v1/pool/user-keys` |
| 加一个 | `POST /api/formal/user-keys` | `POST /api/v1/pool/user-keys` |
| 点头（一次 / 一直 / 先不用） | `POST /api/formal/user-keys/{id}/approve` | `POST /api/v1/pool/user-keys/{id}/approve` |
| 删掉 | `DELETE /api/formal/user-keys/{id}` | `DELETE /api/v1/pool/user-keys/{id}` |

平台那边只给**人**（`pool:user_keys` 权限只含 human）：借别人的钱必须是人的决定。

## 各个窗口都说了什么

`/outputs.html` 是另一个独立窗口（从办公室右栏的「看他们说了什么」打开）。

平台记的是**结果**（谁接了、谁提交了、审没审过），但人真正想看的往往是**窗口自己
说的话** —— 测试怎么报的错、改了哪些文件、最后写了什么。这个页面把这些话按
窗口摊开；一条活做过几次，这里就摊几次（新的那次在前），同一条活的几轮收在
一起，不把标题重复三遍。默认只显示"做完了的"，切到「全部」也能看没做成的那些轮次。

分组用的是**这一次尝试是谁做的**，不是这条活现在的主人 —— 改派过之后这两者不是
一回事，而"谁当时说了什么"才是这里要的。

数据一次取回：浏览器只发一个请求（`GET /api/formal/outputs`），网关在服务端
并行取每条活每一次尝试的 `/tasks/{id}/output?attempt_id=…`。attempt_id 是算出来
的（`{task_id}.a{attempt_no}`），不用先捞一遍事件才知道有哪些尝试。
页面只读，不发任何写。

## 与管理员对话

平台只有**群消息 + @mention** 这一个通道（R10 §19），没有私信。所以"直接和管理员
说话"在这里是：一条 @管理员 的群消息，加上一个把「我 @ 的」和「管理员回的」挑出来看的
面板。界面里不假装有私聊。

## 冒烟脚本

`npm run verify` 会跑 `scripts/verify-*.mjs`。它们分两类：

- **不依赖平台**的：API 全被拦截，验证界面的行为（版式不重叠、对话框关得掉、
  面板不代管理员做决定……）；
- **依赖在线平台**的：需要一个跑着的正式平台（默认 `http://127.0.0.1:4174` 的预览 +
  `http://127.0.0.1:8848` 的平台后端）。平台不可达时这些脚本会失败，那是环境问题，
  不是代码问题。

没有在线平台时 `npm run verify` 会以非零退出（那几条是环境依赖，不是代码失败）。

已知的环境依赖：脚本要 node + playwright；`verify-anchors` 还要 5197 端口的 dev
server；`verify-harness-connections` 断言本机 ollama 有 `qwen3:4b`；
`verify-project-workspace` 断言平台在册窗口的数量。

`verify-office-user-keys` 覆盖「自己的 API key」：列表上没有 key 本身 / 平台来问时
是一张人话卡片且三个按钮都在、且那张卡在右栏最上面 / 点头失败**看得见**（以前点完
跟没点一样）/ 点头打的是 approve+once 且问题消失 / 正在填的表单在重画后不丢 /
窄屏（<1180px）也够得着额度池。

## 环境变量

网关是服务端配置，故意**不带** `VITE_` 前缀（带前缀会进前端产物）：

```
OFFICE_FORMAL_UPSTREAM=http://127.0.0.1:8848
OFFICE_FORMAL_TOKEN_FILE=<操作员令牌文件>
OFFICE_FORMAL_ADMIN_TOKEN_FILE=<管理员令牌文件>
OFFICE_FORMAL_PROJECT=112
OFFICE_FORMAL_GROUP=office-formal-2
```
