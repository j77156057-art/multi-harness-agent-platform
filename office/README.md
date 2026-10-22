# Agent Office —— 办公室（数据版）

平台的**观察面**：看任务、方案、额度、事件、群聊，以及跟管理员对话。
它不派单、不改派、不审核、不合并 —— 那些是管理员 Harness 与成员自己的动作，
界面只提供**人自己的动作**（请求改派、请求预算豁免、请管理员派单、在群里说话）。

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

## 与管理员对话：群里的与私聊

平台只有**群消息**一种，但它可以带 `direct` 签名（R10 §19）：**定向消息**只有发送方与收件方
看得到，其他成员读不到。

- **私聊窗口**（`office/direct.html`）：独立页面，只列定向消息，发出去也带 `direct`；办公室右栏有“开私聊窗口”入口；
- **群聊带子**：只显示群里的话，定向的那些不混进来；
- **管理员回你**：需要用定向消息发回来，否则你在私聊窗口里看不到：

  ~~~bash
  python -m app.cli say --group <group> --author <admin> \
    --mention <你的 id> --direct --body "回复"
  ~~~

  你的 id 就在管理员收到那条消息的 `sender_id` 里（`app.cli log` 可以看到）。

- **自动回**：管理员 worker（`python -m app.admin.worker`）在跑的时候会自己答——
  它把问题连同**平台的任务账本**交给管理员自己的模型通道，答案仍是一条定向消息；
  同一问题只答一次。账本里没有的事它必须直说“不知道”，模型通道失败时它会私下
  说明失败原因，不会发一条看起来像回答的空话。

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

## 环境变量

网关是服务端配置，故意**不带** `VITE_` 前缀（带前缀会进前端产物）：

```
OFFICE_FORMAL_UPSTREAM=http://127.0.0.1:8848
OFFICE_FORMAL_TOKEN_FILE=<操作员令牌文件>
OFFICE_FORMAL_ADMIN_TOKEN_FILE=<管理员令牌文件>
OFFICE_FORMAL_PROJECT=112
OFFICE_FORMAL_GROUP=office-formal-2
```
