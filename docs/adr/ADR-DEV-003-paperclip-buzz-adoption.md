# ADR-DEV-003：借鉴 Paperclip / Buzz 的边界与唯一业务真相源

状态：**已接受**（MVP 后阶段）
日期：2026-10-07
关联任务：paperclip-buzz-adr（协调者八步计划的第 2 步）
影响范围：`docs/adr/**`、后续借鉴类任务的 scope 基线（adapter / scheduler /
          events / frontend）、`docs/dev-convention.md`（待引用）

---

## 背景

2026-10-07，平台完成 MVP-01～11 全部交付与验收，随后巡检器家族十三员
（`1973238`）与 §18 人类决策面（`514c4b2`，合入 `aa1d0a5`）落地：21 个非终态
的覆盖豁免从 7 收敛到 1（仅 `draft_rejected`，出口属 plan 修订流）。协调者指令
下一阶段：**借鉴 Paperclip 与 Buzz 的能力，按序推进**（先收尾、再 ADR、再原型、
再事件层、再垂直切片、最后才考虑深度集成），并给出明确底线：

> R10 状态机、Claim、Lease、fencing、Verify、Merge Gate 仍然是本项目唯一业务真相源。

风险是具体的：外部项目带着自己的数据模型、状态枚举与事件命名。若"借鉴"滑向
"搬运"，平台上会出现**两个任务真相源**——协调与审计随之失真（同一任务在两边
有不同状态、不同历史，谁对谁错没有判据）。R10 §37.7 的白名单机制（88 项事件、
append-only、闭环检查器逐条命中）本身就是反双源的机械守卫，但它只管**本仓库
内**；本 ADR 管的是**引入方向**。

## 决策

1. **R10 是唯一业务真相源**。任务状态机（§10，23 态 + 守卫列）、Claim 与幂等
   （§11）、Lease / fencing / 双锚点（§13）、事件与投影（§15）、Verify receipt
   （§16）、人类决策面（§18）、Merge Gate（§21）、事件白名单（§37.7）的语义
   一律以 `docs/spec/0.3-R10.md` 为准。**任何借鉴都不得在 R10 之外定义第二套
   任务状态、生命周期或事件命名。**
2. **借鉴 = 借鉴形状，不引入运行时**。外部概念必须先经过"映射到 R10 词汇"才能
   进入实现：能映射到既有事件/状态/表的，复用；映射不到的，走 R10 变更流程，
   不私开旁路。
3. **分域借鉴**（本文档的对照表即是逐点清单）
   - **Paperclip 域**：调度、Adapter、Heartbeat、预算、权限；
   - **Buzz 域**：频道、线程、事件日志、Agent 身份、Git 协作。
4. **深度集成另立 ADR**。引入外部运行时或协议（如 Nostr relay、外部调度器
   本体互联、替换既有事件传输层）不在本 ADR 授权范围内，必须新开 ADR 并经
   协调者裁决；新增事件类型必须走 §37.7 白名单变更，本 ADR 不预授权任何新增。
5. **已有资产优先于重新建设**。八步计划中已在 main 上交付的部分（见对照表
   "状态"列），后续任务只补**真缺口**，不重复实现——"借鉴"的第一产出是差异表，
   不是新代码。

## 借鉴点对照表（事实均经 main 核对，2026-10-07）

### Paperclip 域

| 借鉴点 | 既有资产（main 上已交付） | 状态 | 缺口 / 后续（另案登记） |
|---|---|---|---|
| 调度 | `app/scheduler/**` 十三员巡检（`1973238`：过期租约 / 依赖 / 资源 / aging / blocked / stuck / verify / review / merge / 漂移 / 人类提醒 ×2）+ 独立入口 `python -m app.scheduler --loop` | ✅ | `--loop` 挂 lifespan 的运维接线未做 |
| Adapter | `app/adapter/protocol.py` 十六动词接口 + `http.py`（HTTP 传输）+ 真实 harness 实现（Codex / Claude Code / OpenCode，`ae07649`），`probe`/`capabilities`/`read_context`/`ask_human` 已转正 | ✅ | `accept_help` / `reject_help` / `split_task` 三动词尚无路由（`UnsupportedVerb` 显式失败，traceability 有缺口行） |
| Heartbeat | `POST /adapter/heartbeat`：续租 + `progress_hash` 同事务落库；失联由租约巡检进 stale/reclaim 链 | ✅ | — |
| 预算 | `tasks.max_attempts`（默认 3）+ `budget_override_by`（人类覆盖）+ `attempt_budget_exhausted` 边；verify 队列背压上限 32 | ✅ | 时间/token 级预算未建模（当前无生产者，无消费者） |
| 权限 | `HarnessRegistry` 能力注册表（**自报 ≠ 授权**：未经注册的 probe 判 degraded、零有效能力，MVP-10a）+ §17 secret 扫描（提交/审查/留言三入口） | 🟡 | 认证层缺失：§18 pinned 决策与 harness token 403 语义归未来认证任务（现为显式参数声明制） |

### Buzz 域

| 借鉴点 | 既有资产（main 上已交付） | 状态 | 缺口 / 后续（另案登记） |
|---|---|---|---|
| 频道 | `groups` + `messages`（`post_message` / `list_messages`；全局 seq 有序 + refs 持久化，TRAE `message-global-seq` / `message-refs-persist`） | ✅ | 前端频道视图未建（`frontend/` 仅 MVP-01 骨架 54 行） |
| 线程 | 任务事件流按 `aggregate_id = task_id` 过滤即线程；`messages.task_id` / `refs` 可挂靠 | 🟡 | 无独立 thread 实体（刻意：线程=事件投影，不新增表）；前端未建 |
| 事件日志 | `task_events` append-only（**触发器强制**）+ `payload_hash` + 88 项白名单（§37.7）+ 事件页 API（`/tasks/{id}/events`、`/events`） | ✅ | `caused_by` 因果链未建模（当前因果从事件序 + attempt 上下文可推；是否增设字段另案） |
| Agent 身份 | `harness_id` + `registration_version`（注册史可追，claim 钉版本）+ 事件 `actor_type`/`actor_id` | 🟡 | 无认证层：身份=声明制（同"权限"行） |
| Git 协作 | attempt ↔ worktree 一对一、publish proxy（refs 白名单 + epoch 拒旧写）、integration 分支、Merge Gate、巡检器回收 | ✅ | worktree 回收归操作员（需 archive_root 配置） |

## 事件名对齐（协调者提议 → R10 现状）

协调者给出的 Buzz 风格事件清单（11 项）与 R10 §37.7 白名单逐一对齐如下。
**结论：9 项已有对应，2 项（`project.created` / `agent.joined`）属管理面、
当前不产任务事件；若要进入事件流须走 §37.7 白名单变更（另案），本 ADR 只登记
映射意向。**

| 提议事件 | R10 对应（白名单内） | 说明 |
|---|---|---|
| `project.created` | （无） | 管理面路由建项目；无任务事件 |
| `agent.joined` | （无） | Harness 注册落 registry 表（`registered_at`），不产事件 |
| `task.created` | （无；`plan.approve` 产 ready 任务） | 任务随计划批准生成 |
| `task.claimed` | `claim` | |
| `task.help_requested` | `request_help`（入 needs_help）/ `help_requested`（协作自环） | 两个层次：状态迁移 vs 协作元数据 |
| `task.help_accepted` | `help_accepted` | |
| `task.completed` | `submit_result`（提交）→ 终态 `merge_success` | "完成"语义按 R10 = 已合并 |
| `task.abandoned` | `abandon` | |
| `verify.passed` | `verify_passed` | 同名语义已存在 |
| `review.approved` | `approve` | |
| `merge.completed` | `merge_success` | |

## 后果

### 正面

- 后续"借鉴"类任务（前端频道/线程、事件增强、Adapter 补动词）都有统一的边界
  与映射表可引用，**不会出现两个任务真相源**；
- 对照表把"已有资产"钉在当前 commit 上，"八步计划"中已交付者（Adapter 抽象、
  Codex/OpenCode 接入、统一事件模型、帮助/接手/放弃流程）登记为**已完成**，
  避免重复建设；
- 与 R10 §37.7 白名单机制、覆盖套件的反向守卫形成两层防线：内层管仓库内事件
  封闭性，外层（本 ADR）管引入方向。

### 负面 / 成本

- 外部项目"顺手的抽象"（原生状态枚举、事件名、数据模型）不能直接搬，需要
  翻译层——多一道映射成本，换来的是单一真相源；
- 深度集成被本 ADR 显式限制为"另立 ADR"，对追求快速接入的路线是一种减速带
  （这是刻意的：减速带放在方向选择处，而不是放在实现中段）。

## 回退条件

出现以下任一情况，应重开 ADR 或拒绝该方向：

1. 任何实现路径在 R10 之外定义了第二套任务状态 / 生命周期 / 事件命名 →
   该方向拒收，或先修本 ADR 并获协调者裁决；
2. Paperclip / Buzz 深度集成成为硬需求（如引入 Nostr relay、多调度器互联、
   替换事件传输层）→ 另立 ADR，重评本边界与代价；
3. 提出以外部事件模型替换 §37.7 白名单的提案 → 需协调者裁决 + 白名单变更
   流程（八步计划的第 8 步只允许在本 ADR 的闸门后发生）。

## 兼容性保证

本 ADR **不改变 R10 的任何语义**。以下不变量在借鉴过程中保持不变：

| 约束 | 出处 |
|---|---|
| 状态机 23 态 + 守卫列（未定义迁移必须拒绝） | R10 §10 |
| Claim 幂等：请求级 + 任务项级（per-task 唯一在 `task_claims`） | R10 §11 / §8 |
| Lease 双锚点 + 旧 fencing epoch 一律拒绝 | R10 §13 |
| 事件 append-only（触发器强制）、投影可重建 | R10 §15 |
| receipt append-only + 唯一键 | R10 §16 |
| 人类等待只提醒不放行；pinned 决策规则 | R10 §18 |
| Merge Gate 判据（receipt / review / epoch / 健康度） | R10 §21 |
| 事件白名单 88 项、闭环逐条命中 | R10 §37.7 |

## 关联

- R10 §10 / §11 / §13 / §15 / §16 / §18 / §21 / §37.7（被固定的语义）
- `docs/adr/ADR-DEV-001-stack-deviation.md`（技术栈偏离先例、格式模板）
- `docs/adr/ADR-DEV-002-single-account-acl.md`（临时降级先例）
- `docs/proposals/sweeper-family.md`（调度域最近一笔设计）
- `TASKS.md` 任务卡 `paperclip-buzz-adr`（协调者八步计划的第 2 步）
