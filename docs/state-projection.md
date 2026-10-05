# 状态投影声明（MVP-01 交付物，MVP-03-amend 更新）

本文件登记代码对 R10 §10 状态机的**显式投影**：实现哪些、省略哪些、为什么。

这是 H-1 硬约束（省略必须登记）的执行记录。
**未登记的省略视为违规。**

真相源：`docs/spec/0.3-R10.md` §10（23 个正式状态 + 1 个伪状态；
迁移表 86 行，按 `a/b/c` 多状态写法展开后为 **138 个 state-event-next 三元组**）
代码镜像：`backend/app/spec.py`（枚举）、`backend/app/state/machine.py`（迁移表）

---

## 1. 投影原则

MVP 目标（R10 §0 提到的三条）：

1. 任务不会重复领取；
2. 任务状态可恢复；
3. 代码经过验证后才能合并。

状态机的价值在于闭环：砍状态会让闭环验证（每个非终态有出边）失去意义。
因此 MVP-03 交付时选择**在迁移表层面全量投影 R10 §10**——纯表声明零运行时
成本，却让"代码允许的边"与"规范允许的边"可以机械对拍。状态机模块
（`app/state/machine.py`）只做事件校验与下一状态投影，不发 SQL、不改任务行。

---

## 2. 状态投影：23 + 1 全量

R10 §10 的 **23 个正式状态全部投影**为 `TaskStatus` 枚举值；伪状态
`child_created` 仅作为 `in_progress/blocked | split` 的 next 出现，
投影时保持当前状态不变（见 `PSEUDO_STATES`），不参与可达性检查。

| 类别 | 状态 |
|---|---|
| 计划 | `draft`、`draft_rejected`、`ready_waiting_deps` |
| 调度 | `ready`、`queued`、`claimed` |
| 执行 | `in_progress`、`needs_help`、`blocked`、`verifying`、`awaiting_human` |
| 评审/集成 | `awaiting_review`、`changes_requested`、`approved`、`merging` |
| 漂移 | `plan_drift`、`impl_drift` |
| 恢复 | `stale`、`reclaiming`、`abandoned`、`escalated` |
| 终态 | `merged`、`cancelled`（唯一两个无出边状态） |
| 伪状态 | `child_created`（split 幂等出口，非 `TaskStatus`） |

---

## 3. 迁移边投影：86 行 = 138 个展开三元组，全量零省略

`default_transition_table()` 的行序与 R10 §10 迁移表逐行对应，
每行用行号注释标注（如 `# R10 L421`）。多状态写法
（如 `draft/ready/queued/... | cancel`）在对拍时按 `/` 展开。

机械保证（任一侧单独改动测试即红）：

- `backend/tests/test_transition_mirror.py`
  - 代码三元组集合 == R10 §10 展开三元组集合（缺失边/多余边都报 P1）；
  - 展开总数固定为 138；
  - §10 出现的每个状态名/事件名都必须在代码枚举内登记（禁止自造名字）；
- `backend/tests/test_spec_mirror.py`：枚举值 ⊆ R10 §37.7 allowlist（M74/O13/R1）。

典型易错点（均有行为级对拍，见 `test_state_plan.py`）：

- `cancel` 白名单是 **14 个状态**，不含 `stale/reclaiming/awaiting_human`
  （这三者用 `human.cancel`）、`escalated`、`draft_rejected`、`ready_waiting_deps`；
- 漂移事件只从 7 个状态出发（claimed/in_progress/changes_requested/
  awaiting_review/verifying/approved/merging），**不含 blocked/needs_help**；
- `dependency_missing` 只从 `in_progress` 出发；
- `escalation_reminder` 只是 `escalated` 自环。

---

## 4. 表声明 vs 运行时执行：副作用按 MVP 批次落地

迁移表全量投影不等于全部副作用都已执行。每条边的 `guard` 与 `side_effects`
目前是**声明字符串**（`TransitionTable.validate()` 要求非空），真实的意图
创建、Lease 签发、worktree 回收等运行时行为在后续 MVP 批次落地：

| 批次 | 落地的边/副作用 |
|---|---|
| MVP-04 Claim/Lease | `claim*`、`start`、`lease_expire`、`reclaim`/`take_over`、`release`、attempt/Lease/resource 副作用 |
| MVP-06 Verify | `submit_result`、`verify_*`、`differential_not_applicable` 与 awaiting_human 恢复边 |
| MVP-07 Review/Merge | `approve`、`review_*`、`merge_*`、冻结/解冻与 revert 副作用 |
| MVP-08 及以后 | plan 生命周期（reject/revise/supersede）、split、help 协作链等 |

在对应批次落地前，这些边可以通过纯投影校验，但不得声称其副作用已执行。

---

## 5. 投影与 R10 的差异登记

| 项 | R10 | 当前代码 | 处置 |
|---|---|---|---|
| 正式状态 | 23 + 1 伪 | 23 + 1 伪，全量投影 | 无省略，见 §2 |
| 迁移三元组 | 86 行 / 138 展开 | 86 行 / 138 展开，零差异 | `test_transition_mirror.py` 对拍 |
| 事件 allowlist | 88（M74/O13/R1） | `EventKind` 登记全部 74 个 M 事件 | `test_spec_mirror.py` 校验封闭性 |
| 副作用执行 | 强制 | 仅声明，按 §4 批次落地 | 各 MVP 交付时回填执行证据 |
| 三账户 ACL | 强制 | 单账户 + `--insecure-single-account` | MVP-05 交付时必须写明临时措施与补齐清单 |
| baseline differential | base 失败 + head 通过 | MVP 先做 head 单侧 | MVP-06 交付时标记 flaky 判定为阶段 2，**不得静默跳过** |
| integration_health 状态机 | green/red/reverting/retesting | MVP-07 简化版 | 必须保留 `revert_commit.tree == failed_merge.first_parent` 机械断言 |

---

## 6. 变更流程

本文件或迁移表的任何修改都必须：

1. 在 commit body 引用 R10 章节；
2. 先改 R10 正文（规范先行），再改代码与本文件——禁止在代码里自造状态/事件名；
3. 通过 `test_transition_mirror.py` 对拍；新增/删除边会同步改变 138 计数，
   必须在 commit message 中说明规范依据。
