# 状态投影声明（MVP-01 交付物）

本文件登记 MVP 对 R10 §10 状态机的**显式投影**：实现哪些、省略哪些、为什么。

这是 H-1 硬约束（H-1硬约束要求省略必须登记）的执行记录。
**未登记的省略视为违规。**

真相源：`docs/spec/0.3-R10.md` §10（23 个正式状态 + 1伪状态，86 条迁移边）
代码镜像：`backend/app/spec.py`

---

## 1. 投影原则

MVP 目标（R10 §0 提到的三条）：

1. 任务不会重复领取；
2. 任务状态可恢复；
3. 代码经过验证后才能合并。

因此投影的判定标准是：**能否支撑这三条**。与三条无关的状态可以在 MVP 省略，
但"与三条间接相关"的一律保留 —— 省略后留下的洞比多写一个状态更危险。

---

## 2. MVP 全量实现的状态（保守起步）

MVP-03 阶段**先实现 R10 的核心主干**，不删状态。理由：状态机的价值在于闭环，
砍状态会让闭环验证（每个非终态有出边）失去意义。

| 状态 | MVP 阶段 | 说明 |
|---|---|---|
| `draft` | ✅ 首批 | 任务创建初始态 |
| `ready` | ✅ 首批 | 可领取 |
| `claimed` | ✅ 首批 | 已分配 owner |
| `in_progress` | ✅ 首批 | 开发中 |
| `verifying` | ✅ 首批 | Verify Worker 持有 |
| `awaiting_review` | ✅ 首批 | Reviewer 持有 review Lease |
| `changes_requested` | ✅ 首批 | 返工，必须新建 attempt |
| `approved` | ✅ 首批 | 已过 review |
| `merging` | ✅ 首批 | Merge Worker 持有 merge Lease |
| `merged` | ✅ 首批 | 终态 |
| `cancelled` | ✅ 首批 | 终态 |
| `blocked` | ✅ 首批 | 有独立巡检器（R10 §10 硬规则） |
| `escalated` | ✅ 首批 | 人工裁决 |

## 3. MVP 后续批次实现

| 状态 | 批次 | 依赖 | 不能省略的理由 |
|---|---|---|---|
| `ready_waiting_deps` | MVP-03b | MVP-02 | **R10-05 P1 修复项**。R9 缺 `block_timeout` / `orphan_detected` 两条出边时，上游 cancelled 会导致下游永久卡死且不参与 aging。省略这个状态等于把 R10 的修复扔掉 |
| `queued` | MVP-04 | MVP-04 | 资源预占失败的合法出口。省略后 `ready → claimed` 失败时无路可走，任务要么卡死要么绕过资源检查 |
| `stale` | MVP-04 | MVP-04 | Lease 过期出口。**ADR-033 核心不变量**：6 个 owner 状态必须有独立于 owner 的恢复出口 |
| `reclaiming` | MVP-04 | MVP-04 | stale 的中间态，承载"新 Harness 获得新 epoch"的语义 |
| `abandoned` | MVP-04 | MVP-04 | 与 `reopen` 配对（R10-02）。省略 abandoned 则 `reopen` 事件无用武之地 |
| `awaiting_human` | MVP-06 | MVP-06 | `differential: not_applicable` 的**唯一出口**（R10-fix-03 的正向边） |
| `draft_rejected` | MVP-08 | MVP-08 | 方案驳回的可追溯态。MVP 可暂用 `draft` + 事件表达，但需在 MVP-08 补齐 |

## 4. MVP 不实现的状态

| 状态 | 省略理由 | 何时必须补 |
|---|---|---|
| `plan_drift` / `impl_drift` | MVP 阶段 `plan_snapshot` 不可变、`base_commit` 首次 Claim即冻结，漂移检测的输入不足。等MVP-02 落地后再评估 —— **但 `plan_hash_changed` / `impl_hash_changed` 两个事件必须先在 allowlist 占位** | MVP-02 完成后立即评估 |
| `needs_help` | 协作功能（MVP-10b）才有意义。MVP 阶段owner 崩溃走 `stale` | MVP-10b |
| `child_created`（伪状态） | split 功能不在 MVP 范围。**注意**：这是 R10 §10 补充说明表定义的伪状态，只作为 `split` 边的 next 出现，不参与可达性检查 | 阶段 2 |

**省略的连带影响**（必须在 MVP 阶段显式承认）：

1. ❌ 不实现 `plan_drift` / `impl_drift` ⇒ 硬规则"drift 检测覆盖7 态"无法验证。
   `docstate` 的迁移表检查仍会通过（它检查的是**文档**的完整性），
   所以**必须由 MVP-11 在终审报告中显式声明这条不变量未被验证**；
2. ❌ 不实现 `needs_help` ⇒ R10 的 help 协作链（`help_artifacts`、`accept_help` /
   `reject_help`）在 MVP 不可用；
3. ❌ 不实现 `queued` 会破坏 §11 Claim 语义 ⇒ **所以 `queued` 不可省略**，
   它在第3 节而非本节。

---

## 5. 迁移边投影

R10 §10 有 **86 条**迁移边（R10 已把 `next` 全部单值化）。MVP 实现的边集：

| 阶段 | 边数（估） | 说明 |
|---|---|---|
| MVP-03 状态机 | 首批主干约 40 条 | 覆盖第 2 节的 13 个状态 |
| MVP-04 Claim | +15 条 | claim / lease_expire / reclaim / release / abandon |
| MVP-05～07 | +20 条 | publish / verify / review / merge |
| MVP-11 前合计 | ≥ 75 条 | 剩余为 MVP 不实现状态对应的边 |

**硬约束**：任何一条**已实现状态**的出边缺失，都视为 P1 缺陷。
`docstate` 只检查 R10 文档，不检查代码实现 —— 这个缺口由 MVP-11 的
`CORE-STATE-COVERAGE-001`（新增 test_id）补齐。

---

## 6. 投影与 R10 的差异登记

| 项 | R10 | MVP | 处置 |
|---|---|---|---|
| 状态数 | 23 + 1 伪 | 13（首批）+ 7（后续） | 已登记，见 §2/§3/§4 |
| 迁移边 | 86 | ≥ 75 | 已登记，见 §5 |
| allowlist | 88（M74/O13/R1） | 代码侧 `EventKind` 实现 M 的子集 | `test_spec_mirror.py` 校验 |
| 三账户ACL | 强制 | 单账户 + `--insecure-single-account` | MVP-05 交付时必须写明临时措施与补齐清单 |
| baseline differential | base 失败 + head 通过 | MVP先做 head 单侧 | MVP-06 交付时标记 flaky 判定为阶段 2，**不得静默跳过** |
| integration_health 状态机 | green/red/reverting/retesting | MVP-07 简化版 | 必须保留 `revert_commit.tree == failed_merge.first_parent` 机械断言 |

## 7. 变更流程

本文件的任何修改都必须：

1. 在 commit body 引用 R10 章节；
2. 说明为什么该状态在 MVP 可以/不可以省略；
3. 若新增省略项，同步更新 §4 的"连带影响"清单。

**不允许为了让测试通过而扩大投影范围** —— 投影扩大意味着实现负担增加，
但不会让 MVP 的三条验证目标更近。
