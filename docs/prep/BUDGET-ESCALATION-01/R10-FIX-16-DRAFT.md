# R10-fix-16 草案：预算耗尽从 ready/queued 升级

状态：**草案 / 待规范复审**。按 `TASK-ALLOCATION-ARBITRATION.md` §117，本文只是“登记输入”，
不代表规范已批准。本文不新增事件名、状态或 schema；只补 R10 §10 已有
`attempt_budget_exhausted` 事件在 `ready/queued` 上的缺失出边。

---

## 0. 一句话

任务在 `ready`/`queued` 且 `attempt_no >= max_attempts`、无 `budget_override_by` 时，
由 lease sweep 写 `attempt_budget_exhausted` 并升级 `escalated`，使 `human.release`
的预算覆盖入口可达；不再等 3 小时 `aging_timeout`。

## 1. 缺口

- 现状：`claim_one` 对 `ready` 任务返回 `claim.attempt_budget_exhausted`，但不写事件；
  R10 §10 只定义 `stale/reclaiming` 的 `attempt_budget_exhausted` 边。
- 结果：任务永久停在 `ready`，`escalated` 的 release / budget override 入口不可达；
  唯一出口是 `aging_timeout`，默认 90 分钟 × 2 = 180 分钟后才升级，且事件语义错误。
- 触发场景：`human.reopen`、`release`、rework、资源排队把已用满 attempt 的任务放回
  `ready/queued`。

## 2. 规范变更

| 当前 | 事件 | guard | next |
|---|---|---|---|
| `ready/queued` | `attempt_budget_exhausted` | `attempt_no >= max_attempts` 且无 `budget_override_by` | `escalated` |

- 事件名已存在（M 类 allowlist），状态已存在；**不新增 schema**。
- 迁移表 86 → 87 行，展开三元组 138 → 140。
- 触发者：lease sweep（`expire_stale_leases` 内调用 `escalate_budget_exhausted`），
  与 `stale/reclaiming` 同一实现。
- Claim 拒绝本身不写状态；sweep 下一轮升级，避免在失败的 claim 事务里改任务行。

## 3. 实现与测试

- `backend/app/state/machine.py`：新增
  `(READY, QUEUED) + ATTEMPT_BUDGET_EXHAUSTED -> ESCALATED`。
- `backend/app/lease/service.py`：`escalate_budget_exhausted` 扫描四态；
  `expire_stale_leases` 可选 `escalated_out` 供调度器报告。
- `backend/app/scheduler/sweeps.py`：`expired_leases` 报告
  `detail.budget_escalated`，`actions` 计入升级数。
- 测试：`test_lease.py` 的 ready/queued/override 三例；
  `test_sweeper_runner.py` 报告一例；`test_state_coverage.py` 映射；
  `test_transition_mirror.py` / `test_spec_mirror.py` 计数。
- 文档：`docs/spec/0.3-R10.md` §10/§37.7、`docs/state-projection.md`、
  `docs/traceability.md`、`docs/prep/MVP-08/openapi.yaml`。

## 4. 真实平台验证（未提交代码）

- 任务 `FD-0adf6468d27754ac`：`abandoned`（attempt 3/3）→ `human/reopen` → `ready`。
- `claude-code` claim 返回 `claim.attempt_budget_exhausted`，任务仍在 `ready`。
- 约 45 秒后 lease sweep 写 `attempt_budget_exhausted`（actor `scheduler/lease-sweeper`），
  任务变 `escalated`。
- plain `human/release` → `409`；带 `budget_override=true` → `200`，
  `tasks.budget_override_by=office-operator`，`task_decisions(scope='budget_override')`
  记录理由，`human.release` payload 含 `attempt_budget_override`。
- 验证后任务行已恢复为 `abandoned`、清空 override、删除 decision 行；事件日志保留
  `reopen`、`attempt_budget_exhausted`、`human.release` 三条（不可变审计记录）。

## 5. 待复审

- 是否同意把该边同时放在 `ready` 和 `queued`（而非只放 `ready`）。
- 是否接受 `escalated` 后仍只能 `human.release` / `human.adjudicate`（现状）。
- 是否同意“claim 拒绝不写状态、由 sweep 下一轮升级”的时序；若要求 claim 当场升级，
  需要另定 actor 和失败请求的副作用边界。
