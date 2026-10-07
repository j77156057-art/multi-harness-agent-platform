# Paperclip / Buzz 源码核实报告（ADR-DEV-003 修订依据）

状态：已交付（paperclip-buzz-source 任务，2026-10-08）
快照：`block/buzz` @ `c96d707f6efd359e6cfdc34bd93c8b5fb36f1d5d`（Rust，2026-10-07，35k★）；
      `paperclipai/paperclip` @ `ceabc3bc880676c49eee907a8d736f961a1358eb`（TypeScript，2026-10-07，98k★）

---

## 0. 方法与证据链

- **环境**：本机 github.com（网页/克隆）直连被墙；`api.github.com`（文件树）与
  `raw.githubusercontent.com`（文件内容）可达。抓取方式 = 树 API 取 recursive
  tree → 逐文件 raw 下载**文本快照**到**仓外** `D:\study\tools\agent\_refs\`
  （脚本 `fetch_repo.mjs` / `fetch_files.mjs` 一并留存；不进本仓、不 vendor）。
- **覆盖**：Buzz 4431/4445 个文本文件；Paperclip 8482/8494（200KB/文件上限，
  二进制与图片不取；两仓各 14/12 个文件 404 或超限，见 §5）。
- **证据规则**：下文每条结论带快照内 `文件:行号`；**关键条目经人工抽查复核**
  （kind:9、NIP-34 全族、ALL_KINDS=134、ISSUE_STATUSES、BUDGET_SCOPE_TYPES、
  16 内置 adapter、WF-08 TODO）——本报告不是"代理转述"，复核过的见 ★。
- 快照无 `.git`，commit 版本由 API 提交记录钉死（见上）；抓取时间 2026-10-08 凌晨。

## 1. Buzz 核实后的事实

1. **事件注册表**（★）：`crates/buzz-core/src/kind.rs`，`ALL_KINDS` 实测 **134**
   kind（kind.rs:643 起；`ARCHITECTURE.md:193` 自称 "127 kinds"——文档过期）。
2. **频道/线程**：channel = **NIP-29 group**（kind:9007 创建，kind.rs:337；
   kind:9 消息必须带 `["h", channel-uuid]`，`NOSTR.md:348`）；NIP-28 明确
   "not used by Buzz today"（kind.rs:53-54）。线程 = NIP-10 `e`-tag
   root/reply（`nip10.rs:19-45`）；relay 侧用 39005/39006/39007 窗口覆盖事件
   分页（`NIP-CW.md:13-16`）。DM = channel_type=dm + NIP-17 gift wrap。
3. **Agent 身份**：与人同一 keypair 身份模型；`users` 表同表，agent 多出
   `agent_type`/`capabilities`/`agent_owner_pubkey` 列（`schema.sql:167-188`）；
   准入 = NIP-OA 所有者背书 + NIP-AA（`NIP-AA.md:13`，`api/mod.rs:194-225`）；
   频道角色 `MemberRole::Bot` 独立层级（`channel.rs:105-119`）。
4. **Git 协作（★）**：**NIP-34 全族**在册——1617 patch / 1618 PR / 1619 PR tip
   更新 / 1621 issue / 1630-1633 状态（1631 = merged，payload 携带
   `merge_commit`，`builders.rs:1446-1461`）；另有 git 托管（smart HTTP，
   `transport.rs:4-6`）+ 签名提交（NIP-GS）。——比"Git 协作"四个字深得多。
5. **Workflow**：定义 kind:30620（YAML schema，`schema.rs:14-155`，7 种 action
   含 `request_approval`）+ DB 状态机（`RunStatus`/`ApprovalStatus`，
   `buzz-db/src/store/workflow.rs:80-138`）；**但 step/审批事件 46001-46012
   已定义未发射**（★ `buzz-workflow/src/executor.rs:738` TODO WF-08；
   `ARCHITECTURE.md:645` "approval gate 不持久化"）。
6. **43001-43006「agent job」协议**：仅 kind 注册与 feed 分类
   （`kind.rs:520-530`，`feed.rs:275`），**无生产者/消费者**。
7. **集成面（实证）**：WS `REQ`（NIP-01）+ NIP-42 AUTH（`NOSTR.md:42,155`）；
   HTTP bridge `POST /query`、`/events`（NIP-98，`router.rs:325-328`）；
   `buzz-sdk`（类型化构造器，不持钥）与 `buzz-ws-client.publish_event`；
   CLI；现成 bot 例 `examples/countdown-bot`（WS+NIP-42+kind:9000/9）；
   `buzz-acp` 是多 harness 宿主（Goose/Codex/Claude Code，`buzz-acp/README.md:104`）；
   MCP hooks（`docs/MCP_DRIVEN_HOOKS.md`）。

## 2. Paperclip 核实后的事实

1. **形态**：Node 服务 + React UI；Drizzle + PostgreSQL（`packages/db`，314 个
   迁移）；核心表 companies/agents/issues/heartbeat_runs/goals/approvals/
   budget_policies/principal_permission_grants/activity_log（`schema/*.ts`）。
   "org chart" = `agents.reportsTo` 自引用树（`schema/agents.ts:28`）。
2. **任务状态机（★）**：7 态 `backlog/todo/in_progress/in_review/done/blocked/
   cancelled`（`shared/src/constants.ts:189-198`）；边表在
   `doc/SPEC-implementation.md:563-570`；代码侧是 **CAS 守卫**
   （`issues.statusVersion` + `lastStatusDecisionId`，`schema/issues.ts:48-49`）
   + 纯策略仲裁器 `status-arbiter.ts:95`（治理门⇒in_review、契约满足⇒done、
   blocker⇒blocked；race 抛 `NativeStatusRaceError` 重试）。
3. **调度**：DB 唤醒队列 `agent_wakeup_requests`（coalesce/claim/幂等键唯一索引，
   `schema/agent_wakeup_requests.ts:15-86`）+ `wake-queue`/`run-dispatch` 模块
   （admission：proceed/coalesce/defer）；dispatch policy 15 个拒绝码
   （`run-dispatch/domain/policy.ts:51-66`）；每 agent 并发默认 20。
4. **Adapter（★）**：一级抽象 = **adapter**（`ServerAdapterModule`：
   execute/testEnvironment/acp/sessionCodec/…，`adapter-utils/src/types.ts:503-519`）；
   **16 个内置 type**（`builtin-adapter-types.ts:4-21`：acpx_local、claude_local、
   **codex_local**、paperclip_runner、cursor[_cloud]、gemini_local、grok_local、
   hermes[_gateway]、kimi_local、openclaw_gateway、**opencode_local**、pi_local、
   process、http）；外部 adapter = npm 包动态 `import()`（`plugin-loader.ts:181-203`）。
   注意："harness" 在代码里不是一级抽象（仅 `issues.harnessKind` 与产品文案）。
5. **Heartbeat/存活**：`agents.lastHeartbeatAt` + `heartbeat_runs.livenessState/
   livenessReason/lastUsefulActionAt`；纯策略存活分类器
   （`services/run-liveness.ts:29-52`）；active-run watchdog（5/15 分钟阈值，
   `doc/execution-semantics.md:938`）；每 issue 至多一个 `issue_watchdogs`；
   规范明写"心跳收尾必须用持久状态判活"（`doc/SPEC-implementation.md:585`）。
6. **预算（★）**：`budget_policies` scope = **company|agent|project**、
   metric = **只有 billed_cents**、window = 月/终身
   （`constants.ts:875-888`）；**执行点 = 调度前预算预留**
   （`services/budget-reservations.ts:11-59`，冲突即拒）+ dispatch 闸门
   （`budget_blocked`）+ `budget_incidents`（open/resolved/dismissed）。
   README 宣称 goal/issue/provider/model 粒度——**与实际不符**（文档偏差）。
7. **权限/治理**：21 个 `PERMISSION_KEYS`（`constants.ts:1003-1026`）；主体
   `user|agent`；授权表 `principal_permission_grants`；公司角色
   owner/admin/operator/viewer/member；审批 = `approvals` 五态
   （pending/revision_requested/approved/rejected/cancelled）+ 入司审批开关
   `requireBoardApprovalForNewAgents`。
8. **集成面**：REST `/api` + WS 实时事件（`live-events-ws.ts:98`，
   `LIVE_EVENT_TYPES`）+ 出站 webhook（HMAC、仅 HTTPS）+ **MCP server**
   （`POST /mcp/paperclip`，含 create/update/checkout issue 工具）+
   CLI `paperclipai` + 进程内插件事件总线（34 个 `PLUGIN_EVENT_TYPES`，与
   activity_log 接线）+ **`docs/specs/external-task-protocol.md`（Draft v1：与
   Linear/Jira/Asana/… 等外部任务管理器互认的协议草案）**。

## 3. 11 个提议事件名——三方对照（源码核实版）

| 提议名 | R10 现状（白名单） | Buzz 实际（源码） | Paperclip 实际（源码） |
|---|---|---|---|
| project.created | 无（管理面路由） | kind:30621 项目公告（NIP-MP；CLI `projects create` 齐备） | 有同名插件事件（`constants.ts:1687`）+ projects 路由写入 |
| agent.joined | 无 | 无单一 kind；9000 加人 + relay-signed 44100 加入通知 | `join_requests` pending_approval→approved（人）；agent 侧近似 `agent.created` |
| task.created | 无（`plan.approve` 产物） | 43001 **已定义未实现** | `issue.created` 事件 + `POST /issues` |
| task.claimed | `claim` | 43002 **已定义未实现** | `issue.checked_out`；原子 checkout 0 行⇒409 |
| task.help_requested | `request_help`/`help_requested` | 无（仅 moderation escalate） | 无同名；approvals/`ask_user_questions` 近似 |
| task.help_accepted | `help_accepted` | 无 | 无同名；`approval.decided` 近似 |
| task.completed | `submit_result`→终态 `merge_success` | 43004 **已定义未实现** | status `done` + `completedAt` |
| task.abandoned | `abandon` | 43005/43006 **未实现**；46007 cancelled 近似 | status `cancelled` 近似 |
| verify.passed | `verify_passed` ✅ | 无对应事件（push 前 pre-receive 校验是最近似） | 无同名；完成契约 verification 近似 |
| review.approved | `approve` | 46011+46030（**发射待 WF-08**） | `resolve_review` 仲裁路径近似 |
| merge.completed | `merge_success` | **kind:1631 merged（真实在用**，带 merge_commit） | GitHub PR merge 服务（`github-pull-request-merge.ts`） |

**结论**：这 11 个名字是"Buzz 风格"的**命名灵感**，不是 Buzz 现有事件名。Buzz 里
真实在用且最接近的是频道/消息/NIP-34 git 系；`task.*` 系与审批事件属"已定义未
发射"。Paperclip 侧有 4 个可同名复用的事件（project.created/issue.created/
issue.checked_out/budget.incident.*），其余为语义近似。

## 4. ADR-DEV-003 校正点（已写入其 addendum）

1. **举证级别**：初稿"借鉴点"列 = 协调者描述（无外部核实）；本报告起改为
   源码核实，两仓 commit-定版可复现。
2. **两域描述成立且被低估**：Buzz 不只是"频道/线程"——是 134-kind 事件日志 +
   NIP-34 git 全族 + 多 harness 宿主（buzz-acp 跑 Goose/Codex/Claude Code）；
   Paperclip 的"预算/权限"比字样深得多（三级预算 + 预留执行点、21 权限键 +
   五态审批 + governance）。
3. **事件名映射表**：原表（提议名→R10 白名单）仍有效；补 §3 三方对照与
   "并非 Buzz 现有事件名"的诚实标注。
4. **文档偏差**（引用时注意）：Buzz `ARCHITECTURE.md:193` "127 kinds" 过期
   （实测 134）；README "CI posts results" 无 kind 支撑；Paperclip README
   预算粒度夸大；`adapter-plugin.md` 是分支工作日志非规范（正式文档 =
   `docs/adapters/external-adapters.md`，且其示例端点与实现不一致）。

## 5. 深度集成候选（均需按 ADR-DEV-003 决策 4 另立 ADR 裁决）

| # | 候选 | 方式（源码证据） | 风险/边界 |
|---|---|---|---|
| a | **平台事件 → Buzz 频道（单向发布）** | JSONL 导出/CORE-EXPORT-001 → 经 `buzz-sdk` 或 WS `publish_event`（NIP-42 AUTH，agent keypair）以 kind:9 + `h` tag 发到频道；`countdown-bot` 是现成同款示例 | 低：只写 Buzz，不回读、不回写平台；R10 保持唯一真相源 |
| b | 对接 Paperclip `external-task-protocol`（Draft v1） | 见 `docs/specs/external-task-protocol.md`：外部任务管理器的双向互认（外部任务驱动 Paperclip 工作） | **高**：天生双向——与本仓"单真相源"边界冲突；如做须先裁决"哪边是主" |
| c | 平台作为 Paperclip 的 adapter-plugin | Paperclip 外部 adapter = npm 包实现 `ServerAdapterModule`（execute/session） | 形态不合：我们是调度中心，不是模型 harness；不推荐 |
| d | 保守：维持"借鉴不集成" + 知识入库 | 本报告即该项 | 零风险；集成收益推迟 |

**建议**：若协调者要第一个真实集成，选 **a**（单向、有 SDK/示例、可回滚）；
b 明确标注为"需要主从裁决"的远期项。

## 6. 候选借鉴点（未来任务，均非本卡范围）

- **Buzz**：diff 消息 kind:40008（把 patch 带进频道——与我们"任务线程"前端天然互补）、
  canvas(40100)、线程窗口分页(39005-39007)、NIP-10 线程语义。
- **Paperclip**：dispatch policy 的 15 拒绝码矩阵；**预算预留**（调度前 reservation，
  与我们的 `command_idempotency` 是同族模式）；CAS `statusVersion`（与我们的
  fencing epoch 异曲同工）；`external-task-protocol` 的映射方法论。

## 7. 未证实 / 快照缺口（如实记录）

- Buzz：14 个文件 404/超限未取，含 `crates/buzz-relay/src/api/bridge/` 模块根——
  HTTP bridge 的认证细节只能引 router 与文档；README "CI posts results" 无代码
  支撑；43001-43006 与 46001-46012 无发射实现的结论已在 §1（对"已定义"的部分
  仅核实了注册表存在）。
- Paperclip：12 个文件 404/超限未取，含被引的 `server/src/routes/issues.ts` 等
  ——issue 迁移守卫的实现细节与心跳主循环未定位（规范与被引用纯函数已核实）；
  `adapter-plugin.md` 引用的两个文件不在快照。
- 本报告一切结论以快照为准；上游仓库在 commit 之后的变化不在覆盖范围。

## 8. 证据可复现

- 快照与脚本（仓外，非仓库资产）：`D:\study\tools\agent\_refs\`
  （`fetch_repo.mjs`、`fetch_files.mjs`、`buzz_tree.json`、`paperclip_tree.json`、
  `buzz/`、`paperclip/`）。
- 复核命令示例：`git`-less 环境下对任一结论，重新执行树 API + raw 下载即可
  得到同 commit 的同一文件。
