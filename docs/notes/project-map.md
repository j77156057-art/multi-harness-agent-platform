# 这个项目实际有什么（盘点）

状态：2026-11-02 实地统计。作者：助理 agent。

写这篇的原因很具体：我自己在它上面干了两轮活，中间有一次把「界面上人给窗口说话」当成
新能力汇报 —— 其实群聊早就有了。那次之后才发现，**这个项目的规模一直只活在我脑子里，
仓库里没有一处能让人一眼看全**。所以补这一篇，并且每个数字都给了复核命令（§9），
过期了当场就能重算。

## 0. 一句话

一个**多 Harness 协作平台**：把不同的编码 CLI 当作"执行窗口"，用一条受控流程
（方案 → 派单 → 领取 → 租约 → 执行 → 提交 → 验证 → 人审 → 受控合入 → 事件流）把它们
管起来，再加一套给人看的界面（Agent Office）。

## 1. 规模（实测）

| 维度 | 数字 |
|---|---|
| `backend/app` 模块 | **33** 个目录，约 **46 800** 行 Python |
| HTTP 端点 | **120** 条（`api/routes.py` 91 + 池 19 + MCP/人话/转接各 2 + 应用与探针 4） |
| 数据表 / 迁移 | **48** 张表、**20** 个迁移 |
| 规范 `docs/spec/0.3-R10.md` | **1996** 行，且是**封闭集合**（改 §10 边或 §37.7 事件名要同时改文档、`tools/docstate` 镜像、`spec.py`） |
| Office 前端 | 22 个模块 **12 009** 行；网关 `office/server` **2 864** 行 |
| CLI 适配器 | **16 家 CLI** + 1 个平台基座（`platform`） |
| 测试 | **108** 个文件、**1487** 个用例 |

（2026-11-02 按 §9 的命令重算过一遍：这一行之上的数字当时已经漂了 —— 迁移少算了一个、
规范少算了两百多行。数字会随提交变，别当真值背，用完就按 §9 重算。）

## 2. 两种协作方式（项目层面互斥）

| | 派单 | 转接 |
|---|---|---|
| 平台做什么 | 拆任务、派单、校验、审批、合并 | **只当邮局**：按「给<收件人>：」投递 |
| 有没有任务表/工作树 | 有 | 没有 |
| 传输 | HTTP 消息 + 任务状态机 | `<仓库>/relay/` 下的 md 文件 |
| 设计稿 | `docs/spec/0.3-R10.md` | `docs/notes/relay-mode.md` |

## 3. 后端模块（33 个，按职责分组）

数字是行数（**早先一次的逐模块快照，允许有漂移**；总量以 §1 按 §9 重算的结果为准），
括号里是职责一句话。

**主链（派单那一套）**

| 模块 | 行 | 做什么 |
|---|---|---|
| `tasks` | 2313 | REST 层背后的调度侧操作 |
| `claim` | 1546 | 原子领取 + 两层幂等（两个连接抢同一任务只成一个 attempt） |
| `state` | 956 | 纯 R10 状态机投影（可重放） |
| `verify` | 1018 | 验证 worker：baseline 差分 + append-only 回执 |
| `merge` | 771 | Merge worker：集成分支的**唯一写者** |
| `git` | 1183 | 每次尝试一个工作树（创建、隔离、归档） |
| `lease` | 406 | 租约发放/续期/释放/过期扫描 |
| `plan` | 327 | 方案/任务契约的确定性校验 |
| `scheduler` | 1570 | 各类清扫（sweep），含回收站到期清理 |
| `runtime` | 760 | 巡检驱动：Verify 与 Merge 真的跑起来 |
| `liveness` | 411 | 存活扫描 + 「没提交」告警 |
| `human` | 783 | 人的决策面（R10 §18） |
| `events` | 166 | append-only 事件存储 |

**执行窗口（adapter）**

| 模块 | 行 | 做什么 |
|---|---|---|
| `adapter` | 7296 | 适配器协议、CLI worker、输出解析、夹具 |
| `admin` | 3495 | 管理员窗口的自循环（细化、派单、巡检、保持转接运行） |
| `assistant` | 1825 | 平台助手自己的 ReAct 循环 |

**模型与工具**

| 模块 | 行 | 做什么 |
|---|---|---|
| `pool` | 6716 | 额度池：一个 OpenAI 兼容门口，三种协议端点，档位声明/撤销/测试，用户自己的 key |
| `agenttools` | 1969 | 平台工具表（MCP 主路 + 平台代执行的保底路） |
| `usage` | 244 | 成员 CLI 自报的消耗统计 |

**协作与人**

| 模块 | 行 | 做什么 |
|---|---|---|
| `allocation` | 1888 | 分配与传阅分配（R10 §39） |
| `relay` | 1197 | 转接：扫 outbox、投递、线程跳数、守护 |
| `messages` | 169 | 转接信封拆块（纯函数） |
| `buzz` | 717 | Buzz 桥：平台事件 → Buzz 频道（只写不发） |
| `projects` | 163 | 项目生命周期里唯一不在 REST 层的那段：回收站到期清扫（连带任务删、事件流留底） |

**守卫与服务面**

| 模块 | 行 | 做什么 |
|---|---|---|
| `api` | 4078 | REST 路由与错误封套 |
| `auth` | 597 | 权限矩阵（单一决策函数）+ 拒绝审计 |
| `security` | 150 | 出站密钥扫描 |
| `budget` | 817 | 预算护栏（提前预警、超额、阈值事故） |
| `alerts` | 416 | 站内告警（事件触发、HMAC 签名投递、失败重试） |
| `exporter` | 476 | SQLite → JSONL 异步导出 |
| `db` | 336 | SQLite 连接与事务 |
| `cli` | 506 | `python -m app.cli` 入口 |
| `wording` | 637 | 把机器写的文本翻成人话（单一出处） |

## 4. HTTP 面（112 条，按用途分组）

- **项目/群/方案**：`POST /projects`、`/projects/{id}/groups`、`GET /groups`、`/groups/{g}/plans`、
  `POST /plans/{id}/approve|reject`、`GET /plans/{id}/document`
- **任务与尝试**：`/tasks/{id}`、`/tasks/{id}/events|output|diff|forensics|verification|reviews`、
  `POST /claim`、`/heartbeat`、`/submit_result`、`/publish_task_branch`、`/release`、`/abandon`
- **人插手**：`/adjudicate`、`/resume`、`/reopen`、`/cancel`、`/attempt_budget_override`、
  `/review/claim|submit`、`/merge`
- **协助**：`/request_help`、`/accept_help`、`/reject_help`、`/ask_human`、`/report_blocked`
- **传阅与分配**：`/circulation/*`（open/review/revise/close/complete/cancel）、
  `/allocation/*`（open/decide/close/candidates/rounds）、`/application*`、`/material/request`、
  `/decision`、`/decision/defer`
- **额度池**：`/pool/v1/chat/completions`、`/pool/v1/messages`、`/pool/v1/responses`、
  `/pool/declaration`(+withdraw)、`/pool/upstream-tier`(+withdraw)、`/pool/model-test`、
  `/pool/roster-probe`、`/pool/user-keys`(+approve/deny/delete)
- **平台工具**：`GET|POST /agenttools/mcp`
- **转接**：`GET /relay/roster`、`POST /relay/letters`
- **翻译**：`POST /wording/summarize|translate`

## 5. Office（人看的那一面）

21 个模块、视图有：**大厅**（项目卡、平台上已有项目、CLI 安装引导）、**办公室**
（任务表 / 成员状态 / 群聊 / 私聊 / 「需要你处理」/ 模型测试 / 助手）、**转接页**、
**内置终端**、**产出**。

网关（`office/server`）守四条：只回环、同源写校验（`X-Office-Formal`）、凭据只从文件读、
不把机器字段（哈希/commit/内部编号）摆给人看。

## 6. 16 家 CLI + 1 个基座

- **平台内建 6 家**：`claude-code`、`opencode`、`codex`、`codebuddy`、`qoder`、`pi`
- **市面 CLI 10 家**（`adapter/harnesses/market_cli.py`，一份规格表 + 子类）：
  Gemini、Qwen Code、Cursor、Continue、Aider、Copilot、Mistral Vibe、Amp、Crush、iFlow
- **平台基座 `platform`**：不是 CLI，是"平台替模型跑工具循环"（`app/agenttools/` + `platform.py`），
  免费档唯一能干活的路

契约与踩过的坑写在 `docs/notes/cli-adapters.md`（十条契约 + 每条的证据等级）。

## 7. 三条不许破的边界

1. **平台不冒充任何人**：消息作者必须等于令牌主体；转接里"谁说的"写在正文首行。
2. **令牌不进 argv**：只走文件（0600）或子进程环境；命令行里最多出现变量名。
3. **界面不代跑命令、不代拍板**：终端留给人手敲；派单是管理员的动作；合入要人确认。

## 8. 仓库结构（顶层）

```
backend/        FastAPI + SQLite 平台本体（app/ 32 模块，tests/ 106 文件，migrations/ 19）
office/         Agent Office（src 前端 + server 网关 + scripts 校验）
tools/docstate/ 规范镜像与一致性检查（98 项）
docs/spec/      R10 规范（封闭集合）
docs/notes/     设计稿：cli-adapters / mcp-wiring / relay-mode / terminal-design / tool-calling-channels
relay/          转接信箱（运行时产物，不进 git）
data/           运行数据（不进 git）
*.ps1 / *.bat   起服务：setup / start / start-demo / start-formal / start-fresh / start-office
checks-ascii.ps1 校验仓库里的 ps1/bat 是纯 ASCII 且能解析
```

## 9. 怎么复核这些数字（别信我，跑一遍）

```powershell
# 模块数与行数
Get-ChildItem backend\app -Directory | ForEach-Object { $_.Name }
(Get-ChildItem backend\app -Filter *.py -Recurse | ForEach-Object { (Get-Content $_.FullName).Count } | Measure-Object -Sum).Sum

# 端点数量
(Select-String -Path backend\app\api\routes.py -Pattern '@\w+\.(get|post|put|delete|patch)\(' | Measure-Object).Count

# 表与迁移
(Select-String -Path backend\migrations\*.sql -Pattern 'CREATE TABLE IF NOT EXISTS' | Measure-Object).Count
(Get-ChildItem backend\migrations -Filter *.sql).Count

# Office 前端行数
(Get-ChildItem office\src -Filter *.js | ForEach-Object { (Get-Content $_.FullName).Count } | Measure-Object -Sum).Sum

# 测试文件与用例
(Get-ChildItem backend\tests -Filter test_*.py).Count
# 这个 pytest 版本的 `-q --collect-only` 不打印 "N tests collected"，只按文件各打一行
# "tests/x.py: N" —— 所以自己求和，别去找那行不存在的汇总。
$out = .\.venv\Scripts\python.exe -m pytest backend\tests --collect-only -q
$sum = 0; foreach ($line in $out) { if ($line -match '^(tests.+?):\s+(\d+)$') { $sum += [int]$Matches[2] } }
$sum
```

## 10. 这份文档什么时候会过时

数字会随提交变。**看到对不上就以 §9 重算的结果为准，然后回来改这篇**——这正是上一次
出问题的原因：规模只在人的脑子里，谁都没去核对。
