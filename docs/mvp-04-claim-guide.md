# MVP-04 实现指引：Claim 幂等与 per-task 结果

任务：`CORE-CLAIM-001` / `CORE-CLAIM-002` / `CORE-LEASE-001` / `CORE-FENCE-001`
真相源：R10 §11（Claim 事务）、§8（claim_requests）、§12（命令幂等键）、§13（fencing）
状态：Ready，可开工（MVP-02 已验收，MVP-03 提供状态机）

---

## 0. 先读这个：一处已撤回的错误结论

WorkBuddy 在验收 MVP-02 时曾判定「`claim_requests` 缺 per-task 幂等维度」，
并建议拆 `claim_request_results` 表。**该结论错误，已撤回**（见 `TASKS.md`
的「原「缺陷」判定撤回」节）。

实际情况：`task_claims` 已有 `UNIQUE (claim_request_id, task_id)`，per-task 唯一性
已被真正强制。**不要新建表。**

如果你的方案里出现了 `claim_request_results`，说明你踩了同一个坑 ——
请先读 `TASKS.md` 的撤回声明。

---

## 1. 两层幂等各管什么

R10 §11 的关键区分：

| 层级 | 载体 | 语义 |
|---|---|---|
| **请求级** | `claim_requests` | 同一 `claim_request_id` 的**整批**结果；payload 一致性校验 |
| **任务项级** | `task_claims` | 每个 `(claim_request_id, task_id)` 的**单项**首次结果 |

一次批量 Claim 的正确读法：

~~~sql
-- 1. 校验请求级幂等：payload 是否与首次一致
SELECT payload_hash, partial_allowed
  FROM claim_requests
 WHERE claim_request_id = ?;
-- payload_hash 不一致 → 写 idempotency_conflict 并拒绝整个请求（R10 §11）

-- 2. 取 per-task 首次结果（这就是「按任务项返回首次结果」的实现）
SELECT task_id, attempt_id, owner
  FROM task_claims
 WHERE claim_request_id = ?;      -- ← UNIQUE(claim_request_id, task_id) 保证一项一行
~~~

**不需要**额外的 per-task 结果表，`task_claims` 就是。

---

## 2. 事务边界（R10 §11 / ADR-060）

必须用 `BEGIN IMMEDIATE`，**禁止默认 DEFERRED**：

~~~python
conn.execute("BEGIN IMMEDIATE")
try:
    # 事务内顺序不可颠倒
    #   1. 读并锁定 claim_requests
    #   2. 校验 payload_hash
    #   3. 检查任务状态与 attempt 预算
    #   4. 预占资源（失败则只写 queue_reason，不写 owner/attempt/lease）
    #   5. 冻结 base_commit / base_locked_seq
    #   6. 递增 current_epoch
    #   7. 写 owner / attempt / Lease / task_claims / task_events
    conn.execute("COMMIT")
except Exception:
    conn.execute("ROLLBACK")
    raise
~~~

**顺序理由**（不要调整）：

- 先锁 `claim_requests` —— 否则两个同 request_id 的请求会各自看到「首次」
- 再校验 payload —— payload 不一致必须**拒绝整个请求**（R10 §11 明确）
- 预算检查在资源预占**之前** —— 预算耗尽的任务不该占用资源

### 条件 UPDATE 断言（R10 §11）

~~~sql
UPDATE tasks
   SET status = 'claimed',
       owner = ?,
       current_attempt_id = ?,
       current_epoch = current_epoch + 1,
       state_changed_seq = ?
 WHERE task_id = ?
   AND status = 'ready'
   AND current_attempt_id IS NULL
   AND (attempt_no < max_attempts OR budget_override_by IS NOT NULL);
~~~

**必须断言 `rowcount == 1`**，否则 ROLLBACK。这条断言就是"任务不会重复领取"的
实现基础 —— 第二个 Harness 的 UPDATE 会命中 0 行。

> `attempt_no` 与 `budget_override_by` 已在 MVP-02 的 schema 中就位
> （`tasks` 表，NOT NULL DEFAULT 0/ NULL），SQL 可直接用。

---

## 3. 命令幂等键（R10 §12）

不能用 `task_id` 作唯一键，否则返工后新提交会被旧记录挡回：

~~~text
claim:  claim_request_id + task_id      （批量按任务项分别幂等）
submit: task_id + attempt_id + completion_revision
verify: task_id + attempt_id + verify_profile + commit + job_id
review: task_id + attempt_id + reviewer_id + review_revision + reviewed_commit
merge:  task_id + attempt_id + merge_target + merge_revision
publish:task_id + attempt_id + commit + fencing_epoch + idempotency_key
receipt: task_id + attempt_id + commit + verify_profile
~~~

MVP-04 只需实现 `claim` 这一条，但**表结构要按全部 7 条设计**，
否则 MVP-06/07 开工时又是一次迁移。

---

## 4. 自环边：不写状态变更（R10 §10 硬规则）

Claim 路径上有两条自环边，实现时特别注意：

| 边 | 行为 |
|---|---|
| `ready \| claim_deferred_resource → queued` | 写 `queue_reason`，**不写** owner/attempt/Lease |
| `in_progress \| submit_rejected_verify_capacity → in_progress` | 保持 owner Lease，**不产生**状态变更事件 |

自环边**不得**推进 `tasks.state_changed_seq`、**不得**推进 `tasks.waiting_since`。
完整 10 条自环清单见 R10 §10 硬规则。

> 注意 `claim_deferred_resource` 这个事件名是 R10-01 拆出来的。
> R9 时代 `ready | claim` 有两个 next（claimed 与 queued），违反封闭规则，
> R10 已拆为 `claim` 与 `claim_deferred_resource` 两个事件。代码里必须用后者。

---

## 5. fencing（R10 §13 / ADR-033）

每次 Claim 或 reclaim 递增 `current_epoch`。旧 epoch 的写入、publish、merge
**一律拒绝**。

**实现建议**（来自 AO 的 `sessionguard` 范式，见 `docs/ao-capability-audit.md` §3.3）：
把"写之前立刻重读并校验 epoch"收敛到**单一入口**，而不是散落在每个调用点。
AO 的注释说得很直接：

> so the stale-state check lives in one tested place instead of being re-derived
> per call-site

建议在 `app/claim/` 下建一个 `guarded_write()` 之类的单一入口，
所有 epoch 相关写入都走它，并给它写专门的测试。

---

## 6. Lease 巡检与恢复

MVP 阶段的简化（完整要求见 R10 §13）：

| 场景 | MVP 行为 |
|---|---|
| Lease 过期 | 6 个owner 状态 → `stale`（R10 §10 的 `lease_expire` 边） |
| `stale` 后接手 | grace 3 次无心跳 → `reclaiming` → 新 Harness `take_over` |
| attempt 预算耗尽 | `attempt_no >= max_attempts` 且无 `budget_override_by` → `escalated` |
| `needs_help` / `blocked` 中崩溃 | 同样走 `lease_expire` → `stale`（`needs_help` 的三条 help 自环边不参与） |

**6 个 owner 状态**（必须有 Lease 过期出口，ADR-033）：
`claimed` / `in_progress` / `needs_help` / `blocked` / `plan_drift` / `impl_drift`

> MVP 首批只实现 13 个状态（见 `docs/state-projection.md`），但 `lease_expire`
> 这条边要一次做对 —— 它是"任务状态可恢复"这条 MVP 目标的唯一保障。

---

## 7. 必测用例（CORE-CLAIM-002）

R10 §26 的要求：**两个真实 SQLite 连接**并发抢同一任务。

~~~python
# 用Barrier 保证两个线程真正并发（不是串行）
barrier = threading.Barrier(2)

def claim(conn, task_id):
    barrier.wait()          # 两个线程同时冲过
    # ... 各自的 BEGIN IMMEDIATE + 条件 UPDATE
    
# 断言：
# 1. 恰好一个成功（rowcount == 1）
# 2. tasks 表恰好一个 owner、一个 current_attempt_id
# 3. task_attempts 恰好一条新记录
# 4. 恰好一条有效开发 Lease
# 5. current_epoch 只递增了 1（不是 2）
~~~

**注意 SQLite 的锁行为**：`BEGIN IMMEDIATE` 会拿写锁，第二个连接会阻塞到第一个
提交（`busy_timeout` 内）。这不是 bug，是预期行为。测试要用 `busy_timeout`
足够大的连接，否则第二个会直接抛 `database is locked`。

重复 Claim 的断言：

~~~text
- 同 request_id 再次提交 → 返回首次完整结果，不新增attempt
- 同 request_id 但 payload_hash 不同 → 拒绝整个请求 + idempotency_conflict 事件
- 批量 partial_allowed=true → 第二次提交时，成功的项返回首次结果，
  失败的项可以被新 request_id 单独重试
~~~

---

## 8. 提交前

~~~bash
./scripts.sh check     # docstate + ruff + pytest 三道
git branch --show-current   # 必须在 agent/<harness>/MVP-04 上，不能是 main
~~~

`TASKS.md` 的登记要在**开工前**写（规则第 0 条），完成后立刻 ff-merge 进 main
（规则第 8 条）。
