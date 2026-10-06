# 巡检器家族设计（提案）

> 状态：草案，待评审裁决。作者：Qoder（task `sweeper-family`，认领 53）。
> 承接入点：MVP-11 终审报告 §3.2（7 个豁免态）与 §6-P3-1；R10 §10 迁移表守卫列、§13、§20。

## 1. 目标与边界

**目标**：把 R10 里所有「**时间/资源/计数条件满足 → 自动转移**」的出口，统一成一族
可单测、可幂等重放、可独立启停的巡检器（sweeper）；实现后强制点亮
`test_state_coverage.py` 相应豁免项（其反向守卫会红，必须同步更新映射）。

**边界（明确划出，不计入巡检器家族）**：

| 类别 | 例子 | 归属 |
|---|---|---|
| 人类/API 操作面 | `cancel`（14 态）、`human.cancel`、`queue_cancel`、`human.release`、`human.reopen`、`human.adjudicate`、`plan.revise`、`plan.supersede` | 「操作面」任务（§18 人类决策面），本设计只登记其副作用清单的存在性 |
| 事件驱动（非时间/资源） | `submit_result`、`approve`、`request_changes`、`dependencies_pending` 等 | 已实现 |
| 计数熔断（近亲，但触发于写入路径） | `review_round_exhausted`（review_round ≥ max_review_rounds） | 建议在 `submit_review` 写入路径实现（见 §7.2），不在巡检循环里轮询 |

**现状原型**：`app/lease/service.py::expire_stale_leases`（lease 到期 → stale、
`escalate_budget_exhausted`）已实现并被 MVP-04 验收；本设计沿用它的两个模式：
① `now` 可注入用于测试；② 单事务内「条件 UPDATE + 断言 RowsAffected==1」。

## 2. 逐事件规格（全部来自 R10 §10 守卫列，逐字核对）

约定：触发谓词中「等待起点」= `tasks.waiting_since`（进入当前等待态时写入）；
`expected_duration_min` 为 plan 任务字段（默认 90）；新参数见 §5。
所有动作都经 `apply_transition(expected_epoch=…)` 并写一条对应事件（EventKind 已在
§37.7 allowlist 内，零规范改动）。

### 2.1 依赖巡检（ready_waiting_deps 出口）

| 事件 | 触发谓词（spec guard） | 动作与副作用 |
|---|---|---|
| `dependencies_satisfied` | 全部上游 merged **且 output hash 满足契约** | → `ready`；清 `waiting_since`；写事件 payload（upstream ids + hashes） |
| `block_timeout` | 等待超过**上游 expected_duration 之和 ×2** | → `escalated`；写 `escalated_at` |
| `orphan_detected` | 上游**全部 terminal** 但仍无 merged（cancelled/abandoned/draft_rejected 组合） | → `escalated`；payload 记录孤儿集合 |

注：§10 明确「`ready_waiting_deps` 不参与 aging」（line 589），其超时只有 block_timeout 一条。

### 2.2 资源巡检（queued 出口）

| 事件 | 触发谓词 | 动作与副作用 |
|---|---|---|
| `resource_available` | `queue_reason` 对应容量恢复**且调度器在同一事务内成功预占所需资源** | → `claimed`：**完成被延迟的 Claim**——owner=原 claim_request 的 harness，新 attempt/attempt_no+1/epoch/开发 Lease；受原 `claim_request_id` 幂等约束（复用 `_first_pass` 机制） |
| `aging_timeout` | 排队等待超过阈值 | → `escalated` |

**设计要点（复杂度集中在此）**：queued 行的 `owner/attempt/lease` 为空（R10 §11），
sweeper 必须从 `task_claims`（未释放、release_reason=`queued:…`）反查原
`claim_request_id → harness`，在同一事务里完成「资源预占 + 尝试创建 + 转移」。
幂等：原 claim_request 已完成过则整条不再动作；重复巡检 no-op。
**开放问题 O1**：若原请求方已离线（其 harness 心跳过期），是照常授予（Lease 会再过期
走 stale）还是回退到 `ready` 让任何人领取？规范字面是 `claimed`，本设计按字面实现，
建议评审确认。

### 2.3 老化巡检（ready/queued 饥饿）

| 事件 | 触发谓词 | 动作 |
|---|---|---|
| `aging_timeout` | §20：`ready` 持续超过 `expected_duration_min × 配置倍数`（默认 2×）无人领取 | → `escalated`；payload 记录等待时长与倍数 |

### 2.4 阻塞巡检（blocked 超时）

| 事件 | 触发谓词 | 动作 |
|---|---|---|
| `block_timeout` | 阻塞超过 `block_sla_minutes`（新参数） | → `escalated` |

### 2.5 卡住巡检（stuck 指纹，§13 默认 3/5 次）

| 事件 | 触发谓词 | 动作 |
|---|---|---|
| `stuck_threshold_reached` | `in_progress/needs_help/blocked/verifying` 连续 **3** 次巡检无进展 | → `escalated` |
| `stuck_human_threshold` | 同上连续 **5** 次 | → `awaiting_human` |

**「无进展」的机械定义（本设计的核心新件）**：

- 指纹 `fingerprint(task) = hash(该任务最新事件 seq, current_attempt.head_commit, 最近一次
  heartbeat 的 progress_hash)`；任一变化即「有进展」，计数清零；
- 载体：新表 `sweep_progress(task_id PK, fingerprint, unchanged_count, last_checked_at)`
  （迁移一件）；重启后从表恢复，天然跨重启；
- 与 Lease 的关系：卡住 ≠ 失联。心跳正常但内容不动的任务由本巡检处理；
  心跳断了的任务由 lease 巡检（既有）处理——两者互不替代。

### 2.6 验证巡检（verify 侧）

| 事件 | 触发谓词 | 动作与副作用 |
|---|---|---|
| `verify_lease_expire` | verify Lease 到期**且 verify_run intent 对账确认进程已死** | → `verifying`（自环）：**新建 verify_job/job_id/overlay，不新建 attempt、不改 epoch**（§10 边注 line 515） |
| `verify_queue_timeout` | queued verify job 超过 `verify_queue_sla_minutes`（已有=30） | → `escalated`（§10 line 591：**只由 verify queue 巡检器触发**） |

**开放问题 O2**：MVP 没有进程表，`verify_run intent 对账` 的「确认进程已死」以什么为凭？
建议：以 `verify_jobs` 状态 + verify Lease 到期为代理，并在实现任务里显式声明这一近似
（不许静默跳过）。

### 2.7 review / merge 等待巡检

| 事件 | 触发谓词 | 动作与副作用 |
|---|---|---|
| `review_lease_expire` | review Lease 到期且无进行中 review intent | → `awaiting_review`（自环）：重排 review |
| `merge_lease_expire` | merge Lease 到期且无进行中 merge intent | `approved`/`merging` → `approved`：重排 merge |
| `merge_timeout` | 集成测试超过 `integration_test_timeout_minutes`（已有=30） | → `escalated` |
| `review_wait_timeout` | `awaiting_review/approved/merging/changes_requested` 等待超过 SLA（新参数，建议 60m，`changes_requested` 为返工等待） | → `escalated`；§13：**只升级不抢占**已提交的开发任务 |

### 2.8 重取窗口巡检（reclaiming）

| 事件 | 触发谓词 | 动作 |
|---|---|---|
| `nobody_takes` | 接手窗口超时（`reclaim_grace_minutes`，新参数；与既有 `reclaim(grace_elapsed=True)` 的宽限语义对齐） | → `ready`（回到池子） |

### 2.9 人类等待提醒（只提醒，不放行）

| 事件 | 触发谓词 | 动作 |
|---|---|---|
| `human_wait_timeout` | `awaiting_human` 超过 `human_reminder_minutes`（已有=30） | **自环提醒**（`next_reminder_at` 退避，默认 ×2，上限封顶）；§13：不自动放行、不自动取消 |
| `escalation_reminder` | `escalated` 持续未裁决 | 自环退避提醒 |

### 2.10 漂移巡检（点亮 plan_drift / impl_drift 两态）

MVP 未实现漂移探测器，两态不可达（终审报告 §3.2-B）。本设计把它纳入家族：

| 事件 | 触发谓词 | 动作 |
|---|---|---|
| `plan_hash_changed` | 任务绑定 plan snapshot 的 hash ≠ 计划最新快照 hash（且未批准变更单） | → `plan_drift`（自 `claimed/in_progress/verifying/…`，全边已有） |
| `impl_hash_changed` | base_commit 的 impl 指纹（integration HEAD 变化）≠ 冻结值 | → `impl_drift` |

**开放问题 O3**：原 R10 语境里两个 hash 的「冻结值」存在哪（plan_snapshot_id 的 hash、
`base_commit`+`base_locked_seq` 的投影）需要评审敲定；探测频率可低频（如 10× sweeper 周期）。

## 3. 运行器架构

```
app/scheduler/
├── __init__.py
├── runner.py      # SweepRunner：按序调用各 sweep，汇总 SweepReport
└── sweeps.py      # 每个 sweep 一个纯函数：sweep_x(database, *, now) -> SweepReport
```

- **选型：单循环、多 sweep 函数**（不是每用途一个线程/进程）。理由：巡检全部是
  「读条件→条件写」，共用事务/时钟/关停语义；分线程只会引入并发窗口。
- **调用形态（三选一，建议全给）**：
  1. `run_all_sweeps(database, now=…)`——同步入口，测试直接调（沿用 `expire_stale_leases(now=)` 模式）；
  2. `python -m app.cli sweep --once / --loop`——并入既有操作员 CLI；单测/运维都友好；
  3. FastAPI lifespan 后台任务（`sweeper_interval_seconds` 节拍，默认 60s；测试环境禁用）——
     生产便利，非依赖项。
- **时钟**：巡检判据用墙钟字段（`waiting_since`/`lease_until`/`next_reminder_at`），
  `now` 一律参数注入；不做 lease 的双锚点（那是「失联判定」的精度需求，巡检是粗粒度
  治理）。重启语义：**无内存状态**——全部判据读 DB、计数读 `sweep_progress` 表。
- **并发**：每个 sweep 一个 `BEGIN IMMEDIATE` 事务；转移全走 `apply_transition`
  （epoch 条件）+ 条件 UPDATE（RowsAffected==1 断言）。两个实例并行跑巡检不会双触发：
  第二个事务在条件上看到状态已变 → no-op。**巡检器不与心跳/提交互斥**，靠 epoch/
  状态条件碰撞产生单赢家（与既有 lease 巡检同法）。
- **幂等与审计**：每个动作=一条 §37.7 allowlist 事件 + 一次投影转移；payload 里带
  触发谓词的实测值（等待时长、计数等），保证可复现。

## 4. 参数清单（现有 vs 新增）

| 参数 | 现状 | 建议 |
|---|---|---|
| `lease_seconds` / `heartbeat_seconds` / `stale_grace_misses` | 已有 | 不动 |
| `integration_test_timeout_minutes`（30）/ `integration_test_sla_minutes`（60） | 已有 | merge_timeout / 集成侧用 |
| `verify_queue_sla_minutes`（30） | 已有 | verify_queue_timeout 用 |
| `human_reminder_minutes`（30） | 已有 | human_wait_timeout 用 |
| `max_review_rounds`（2） | 已有 | review_round_exhausted 用（写入路径） |
| `sweeper_interval_seconds` | **新增** | 默认 60 |
| `aging_multiplier` | **新增** | 默认 2（× expected_duration_min） |
| `block_sla_minutes` | **新增** | 建议 60 |
| `review_wait_sla_minutes` | **新增** | 建议 60（返工等待同值起步） |
| `reclaim_grace_minutes` | **新增** | 与既有 reclaim 宽限语义对齐，建议 10 |
| `stuck_sweeps` / `stuck_human_sweeps` | **新增** | 3 / 5（§13 默认） |
| `escalation_backoff_multiplier` / `reminder_cap_minutes` | **新增** | 2 / 240（退避上限） |

全部环境变量可覆盖（沿用 `Settings` 约定）。

## 5. 与状态覆盖套件的对接（实现后逐态点亮）

| 状态 | 点亮出口 | 点亮后动作 |
|---|---|---|
| ready_waiting_deps | dependencies_satisfied / block_timeout / orphan_detected | 移出 WAIVERS_NO_EXIT |
| queued | resource_available / aging_timeout | 移出 WAIVERS_NO_EXIT（queue_cancel 仍属操作面，不阻塞点亮） |
| plan_drift / impl_drift | 漂移巡检两事件进入状态 | 移出 WAIVERS_UNREACHABLE |
| awaiting_human | human_wait_timeout（自环提醒）已有 resume 出口；提醒为增补 | 维持映射，增补一列 |
| escalated | escalation_reminder（自环提醒）；真正出口 human.release/adjudicate 仍属操作面 | 维持 WAIVERS_NO_EXIT，但在报告中降级为「仅缺人类面」 |
| draft_rejected | 无（plan.revise 属操作面） | 维持豁免 |
| abandoned | 无（reopen 属操作面） | 维持豁免 |

净效果：豁免 7 → **3**（draft_rejected、abandoned、escalated 各剩"人类面出口"一个理由），
且这三个的理由收敛成同一件事（§18 人类决策面任务），可并作一张下阶段卡。

## 6. 任务拆分建议（实现阶段）

| 任务建议 | 内容 | 依赖 | 建议 test_id |
|---|---|---|---|
| `sweeper-runner` | runner + 时钟/事务骨架 + `sweep_progress` 迁移 + CLI/lifespan 接入 + 参数集中 | MVP-11 | `SCHED-SWEEP-001` |
| `sweeper-deps-resources` | 2.1 依赖巡检 + 2.2 资源巡检（含延迟 Claim 完成，O1 裁决后） | runner | `SCHED-SWEEP-002` |
| `sweeper-aging-stuck` | 2.3/2.4/2.5（含卡住指纹） | runner | `SCHED-SWEEP-003` |
| `sweeper-workers` | 2.6/2.7（verify/review/merge 等待与租约重排；0.5 个近似声明） | runner | `SCHED-SWEEP-004` |
| `sweeper-drift` | 2.10（O3 裁决后） | runner | `SCHED-SWEEP-005` |
| `human-ops` | §18 操作面（cancel 家族、reopen、human.release/adjudicate、ask_human）| 独立 | 另案 |

建议顺序：runner → deps-resources → aging-stuck → workers → drift。
每个任务都可独立合并（巡检器之间零共享代码，除 runner）。

## 7. 一致性与风险

1. **与 lease 巡检的次序**：每轮先跑 lease 巡检（既有），再跑其余——stuck 判定要看到
   刚被标记 stale 后的状态，避免同轮双动作。runner 固定顺序并测试钉死。
2. **计数熔断归属（review_round_exhausted）**：不在巡检循环；在 `submit_review`
   `request_changes` 写入时判断 `review_round ≥ max_review_rounds` → 走 escalated 边。
   本设计只转移归属，不重复实现。
3. **时钟跳变/夏令时**：墙钟字段 + UTC；判定全部是「now - waiting_since > 阈值」，
   跳变最多推迟/提前一轮 60s 节拍，可接受（写进 runner docstring）。
4. **verify 进程对账近似（O2）**：明示近似，禁止静默。
5. **O1**（离线请求者的延迟 Claim）与 **O3**（漂移指纹的冻结值落点）需评审裁决后
   对应实现任务才开工。
6. 本设计的实现全部落在 `backend/app/scheduler/**` + 1 个迁移 + `app/cli` 接线；
   不触碰 claim/lease/verify/merge 的既有事务边界（只读它们的状态与 lease 表）。

## 8. 验收视角（预测会被问的问题）

- 「巡检器会不会抢走人力活」：不会——所有边都是 §10 已有边，动作是转移+事件，
  零新语义；参数全部显式默认 + 可关。
- 「两个调度器实例会双跑吗」：条件 UPDATE + epoch 断言保证单赢家；可加一条
  双实例并发的机械测试（deps-resources 任务交付）。
- 「为什么不是 cron / 外部调度」：巡检动作必须与平台同事务（资源预占、epoch），
  外部 cron 只能通过 API 间接做，窗口更差。
