# MVP-08 幂等响应约定（prep 草案）

真相源：R10 §11（Claim、批量 Claim 和幂等，含 R10-fix-09 的两层幂等职责划分）、
§12（Attempt 和命令幂等）、§8（`claim_requests` / `task_claims` /
`command_idempotency` 字段）、§13（fencing epoch）、§15（事件 seq）、
§16（receipt 唯一键）

> **R10-fix-09 提醒（本文件已按修正后的口径写）**：`claim_requests` 是**每请求一行，
> 不含 `task_id` 列**。per-task 唯一性与 per-task 首次结果落在
> **`task_claims`** 的 `unique(claim_request_id, task_id)`，不在 `claim_requests` 上。
> 不要把复合键放回 `claim_requests`，也**不要**为此新增
> `claim_request_results` 之类的表 —— 该拆表方案已被否决（R10 §29.6 要求不再往正文加表），
> 且 `001_initial.sql` 已实现 `task_claims` 上的约束。

---

## 1. 幂等键构成（逐字抄自 §12，不得增删）

~~~text
claim:    claim_request_id + task_id（批量请求按任务项分别幂等）
submit:   task_id + attempt_id + completion_revision
verify:   task_id + attempt_id + verify_profile + commit + job_id
review:   task_id + attempt_id + reviewer_id + review_revision + reviewed_commit
merge:    task_id + attempt_id + merge_target + merge_revision
publish:  task_id + attempt_id + commit + fencing_epoch + idempotency_key
receipt:  task_id + attempt_id + commit + verify_profile
~~~

§12 的理由必须一并记住：**不能只用 `task_id` 作为 completion 或 submit 的唯一键，
否则返工后新提交会被旧记录挡回。** 这条是 MVP-06/MVP-07 最容易实现错的地方 ——
`changes_requested` 后新 attempt 的 submit 会因为 `task_id` 相同而被判定为重放，
返回上一次的失败 receipt。

### 1.1 键的存储落点（§8，R10-fix-09 口径）

| operation | 存储表 | 唯一键 |
|---|---|---|
| `claim`（请求级） | `claim_requests` | `claim_request_id` PRIMARY KEY（**每请求一行，不含 `task_id` 列**） |
| `claim`（任务项级） | **`task_claims`** | `unique(claim_request_id, task_id)` |
| `submit` / `verify` / `review` / `merge` / `publish` | `command_idempotency` | `(operation, idempotency_key)` |
| `receipt` | `verification_receipts` | `unique(task_id, attempt_id, commit, verify_profile)` |
| `publish` 的副作用 | `side_effect_intents` | `unique(type, idempotency_key)` |

R10-fix-09 把 claim 的单行 `claim_requests` 唯一键换成了**两张表分工**：请求级一行在
`claim_requests`，任务项级每项一行在 `task_claims`。§11 的原文口径：

> `claim_requests` 是每请求一行，不含 `task_id` 列：请求级的"这次请求的首次结果"落
> `claim_requests.first_result_json`；**per-task 的首次结果**按 `task_claims` 的
> `unique(claim_request_id, task_id)` 落地，重试时以
> `SELECT * FROM task_claims WHERE claim_request_id = ?` 逐任务项回放，**不从 JSON 里反解**。
> 批量 Claim 的每个任务项都必须留下一行 `task_claims`，失败项同样留行并带释放原因。

这直接推出三条实现约束：

1. `first_result_json` **只承载请求级**的首次响应，不能拿它反解任务项结果 —— 反解会
   与 `task_claims` 形成两份真相源，且批量项数变化时两者会不一致；
2. 回放必须**逐任务项**读 `task_claims`，所以 `task_claims` 需要能表达"这一项当时
   是什么结果"，包括失败项；
3. **失败项也必须留行**（带释放原因），不能只在成功时才写。否则重试回放时，
   "当时失败"这件事无处可读，客户端会误以为该项从未被处理。

`receipt` 和 `publish` 有**两处**唯一约束（`command_idempotency` + 各自的业务表）。
这不是冗余：`command_idempotency` 管"同一命令返回同一结果"，业务表的唯一键管
"事实不被重复写入"。§16 明确 receipt "同一 attempt 的重复 receipt 返回首次结果；
不同 commit 必须生成独立 receipt"—— 前半句靠 `command_idempotency`，后半句靠
`verification_receipts` 的唯一键。**两个都必须有，缺一个就会出 §16 禁止的覆盖行为。**

---

## 2. 双层 Claim 幂等（§11 的硬要求）

清单 MVP-04 的完成标准点名了这一条，API 契约必须原样支持：

> 重复 `claim_request_id` 返回首次结果，批量时**按任务项**返回首次结果
> （`unique(claim_request_id, task_id)`，漏了这条批量重试语义落空）

按 R10-fix-09，括号里那个复合键属于 **`task_claims`**，不属于 `claim_requests`。
两层各读各的：请求级读 `claim_requests.first_result_json`，任务项级读
`task_claims` 的行。

### 2.1 语义

| 场景 | 行为 |
|---|---|
| 同 `claim_request_id`，同 `task_ids`，同 `payload_hash` | 返回**首次的完整结果**，逐项标记 `replayed: true` |
| 同 `claim_request_id`，`task_ids` / 参数 / `payload_hash` 任一不同 | 拒绝**整个请求**，写 `idempotency_conflict` 事件，错误码 `idempotency.conflict` |
| 同批次部分失败后，客户端想只重试失败项 | **必须使用新的 `claim_request_id`**（§11 明确："客户端可以使用新的 request_id 只重试失败项"） |
| 同 `claim_request_id` 并发到达两次 | 第二个在 `BEGIN IMMEDIATE` 事务内阻塞在 `claim_requests` 行锁上；第一个提交后，请求级从 `claim_requests.first_result_json` 取，任务项级按 `SELECT * FROM task_claims WHERE claim_request_id = ?` 逐项回放；**不得**返回 409 |

最后一行是实现要点：并发同键不是冲突，是重放。返回 409 会让 Adapter 以为
claim 失败而放弃任务，但任务其实已被自己领到 —— 这会直接造成任务悬挂到
Lease 过期，是 MVP-04 `CORE-CLAIM-002` 要抓的缺陷类型。

回放必须**逐任务项读表**而不是反解 JSON：批量项数与 JSON 里的数组长度可以不相等
（失败项留行、成功项也留行，但 JSON 只承载请求级结果），反解会在两者之间制造
第二份真相源。

### 2.2 `partial_allowed`

- 默认 `false`（all-or-nothing），**必须落库**（§11 R8 Claim 事务约束）；
- `false` 时任一项失败 → 整个事务回滚 → 响应里 `items[]` 每项都带同一个 error，
  HTTP 状态取首个失败项（README Q-4，已由 R10-fix-09 部分回答：失败项同样留
  `task_claims` 行并带释放原因，所以逐项结构是必须的，不是可选的美化）；
- `true` 时逐项独立返回结果、逐项独立写事件，`items[].outcome` 可以是
  `claimed` / `queued` / `rejected` 的任意组合；
- `partial_allowed` 参与 `payload_hash` 计算 —— 同键不同 `partial_allowed` 是冲突；
- **无论 `partial_allowed` 取值，每个任务项都必须留下一行 `task_claims`**
  （§11 原文："批量 Claim 的每个任务项都必须留下一行 `task_claims`，失败项同样留行
  并带释放原因"）。这条与 all-or-nothing 不矛盾：事务回滚时整批都不留行，
  请求级幂等由 `claim_requests` 承担；事务提交后逐项留行。

---

## 3. `payload_hash` 的计算规则

§11 要求"同一 request_id 但 task_ids、参数或 payload_hash 不同必须拒绝"，
但没有定义 `payload_hash` 怎么算。不定义就会导致各 Adapter 算法不同，
`idempotency.conflict` 变成随机故障。契约固定如下：

1. 取请求 body 的 JSON 对象；
2. **剔除**三个字段：`claim_request_id`、`idempotency_key`、`payload_hash`
   （键本身不参与自身哈希，否则无法在收到请求前计算）；
3. 按 RFC 8785（JSON Canonicalization Scheme）序列化：
   对象键按 Unicode 码点升序、无空白、数字按 ECMAScript `Number::toString`、
   字符串按 UTF-8 不做额外转义；
4. `payload_hash = "sha256:" + hex(sha256(canonical_bytes))`。

服务端**必须自己重算**并与客户端提供的值比对：

- 一致 → 正常处理；
- 客户端未提供 `payload_hash` → 服务端计算并落库（不拒绝，向后兼容手写调用）；
- 不一致 → `idempotency.conflict` + `idempotency_conflict` 事件，
  `details` 只放 `expected_hash` / `actual_hash`，**不放** body 差异内容（D-8）。

RFC 8785 而不是"排序键 + 去空格"的原因：后者对数字（`1.0` vs `1`）、
Unicode 转义（`é` vs `\u00e9`）、嵌套数组的处理各语言不一致，Python 与
JS Adapter 会算出不同 hash。Python 侧可用 `json.dumps(obj, sort_keys=True,
separators=(',',':'), ensure_ascii=False, allow_nan=False)` 近似，但
**必须**先做一次数字规范化，否则 `allow_nan=False` 之外仍有 `1.0/1` 分歧。
这一点留给 MVP-08 实现时写测试固定。

---

## 4. 重放响应约定

### 4.1 通用规则

1. 重放返回**首次结果的逐字节副本**，不重新执行业务逻辑，不写新事件。来源按
   R10-fix-09 分两层：请求级从 `claim_requests.first_result_json` 读出直接返回；
   claim 的**任务项级**从 `SELECT * FROM task_claims WHERE claim_request_id = ?`
   逐项回放，**不从 JSON 里反解**；
2. HTTP 状态码**与首次相同**（首次 200 → 重放 200；首次 503 → 重放 503）。
   不得把重放改成 201 或 200 —— §11："返回第一次的完整结果"；
3. 额外加两处标记，供 Adapter 与测试区分：
   - 响应 header：`Idempotency-Replayed: true`
   - 响应 body：顶层 `replayed: true`，批量时 `items[].replayed` 逐项标记；
4. 重放响应带 `first_created_seq`（首次写入的事件 seq），**不带**新的 `seq`
   —— 因为没写新事件。这样客户端的 `since_seq` 游标不会因为重放而误推进；
5. 重放**不消耗** attempt 预算、**不递增** `fencing_epoch`、**不创建** attempt。

第 5 条是最关键的正确性要求：如果重放走了正常路径，`tasks.attempt_no` 会被
第二次 +1，三次网络重试就烧光 `max_attempts=3`（§13），任务直接进
`attempt_budget_exhausted`。

### 4.2 Claim 成功响应体

~~~json
{
  "claim_request_id": "cr-2026-10-05-001",
  "replayed": false,
  "first_created_seq": null,
  "created_seq": 1042,
  "partial_allowed": false,
  "outcome": "claimed",
  "items": [
    {
      "task_id": "T-006",
      "outcome": "claimed",
      "replayed": false,
      "attempt_id": "T-006.a1",
      "attempt_no": 1,
      "owner": "harness-1",
      "fencing_epoch": 3,
      "lease_token": "lsn_9f2c...",
      "lease_until": "2026-10-05T07:15:00Z",
      "base_commit": "a1b2c3d4",
      "base_locked_seq": 980,
      "registration_version": "reg-003",
      "created_seq": 1042,
      "queue_reason": null,
      "error": null
    }
  ]
}
~~~

`outcome` 顶层值 = 全部项的归并：全 `claimed` → `claimed`；含 `queued` 且
`partial_allowed=true` → `partial`；`partial_allowed=false` 且含失败 → `rejected`。

### 4.3 Claim 被延迟（queued）响应体

~~~json
{
  "claim_request_id": "cr-2026-10-05-002",
  "replayed": false,
  "created_seq": 1050,
  "partial_allowed": false,
  "outcome": "queued",
  "items": [
    {
      "task_id": "T-007",
      "outcome": "queued",
      "replayed": false,
      "queue_reason": "worktree_capacity",
      "created_seq": 1050,
      "attempt_id": null,
      "owner": null,
      "fencing_epoch": null,
      "lease_token": null,
      "lease_until": null,
      "base_commit": null,
      "error": null
    }
  ]
}
~~~

HTTP **200**。`owner` / `attempt_id` / `lease_token` / `base_commit` 全为 `null`
—— §11："预占失败时只进入 queued，不写 owner、attempt、Lease 或开发副作用"。
`base_commit` 也必须为 null，因为 §11 规定 base_commit 只在**成功** Claim 的
同一事务中冻结。

`queue_reason` 取值需要 MVP-02 在 schema 里定义为封闭集合（§8 有
`tasks.queue_reason` 字段但正文未给取值域）—— 见 README §5。

### 4.4 submit_result 成功响应体

~~~json
{
  "replayed": false,
  "created_seq": 1180,
  "task_id": "T-006",
  "attempt_id": "T-006.a1",
  "completion_revision": 2,
  "status": "verifying",
  "event_name": "submit_result",
  "verify_job_id": "vj-0031",
  "lease_released": true
}
~~~

`lease_released: true` 依据 §13："submit_result 成功后立即释放开发 Harness 的
owner Lease"。

**失败容量场景**（`submit.verify_capacity_full`）的响应必须显式写
`lease_released: false`，即使它是错误响应：

~~~json
{
  "error": {
    "code": "submit.verify_capacity_full",
    "message": "verify capacity exhausted, owner lease retained",
    "retryable": true,
    "task_id": "T-006",
    "attempt_id": "T-006.a1",
    "seq": 1181,
    "event_name": "submit_rejected_verify_capacity",
    "details": { "lease_released": false, "active_verify_jobs": 32 }
  }
}
~~~

带 `Retry-After: 60`。把 `lease_released` 放进错误详情而不是只写在文档里，
是为了让 Adapter 的实现无法忽略它 —— 这是 README D-2 的落地点。

### 4.5 publish_task_branch 响应体

~~~json
{
  "replayed": false,
  "created_seq": 1220,
  "task_id": "T-006",
  "attempt_id": "T-006.a1",
  "branch": "agent/g001/harness-1/T-006/T-006.a1",
  "commit": "e5f6a7b8",
  "fencing_epoch": 3,
  "intent_id": "int-0042",
  "intent_status": "done",
  "generated_commit": null,
  "patch_hash": null
}
~~~

宿主模式为 `patch_apply` 时（ADR-059，也是清单 §5 认定的**唯一可行 AO 路径**），
`generated_commit` 非空且**它才是** `head_commit`，`patch_hash` 非空，
`commit` 是客户端上传的 patch 所基于的 commit。Adapter 必须用
`generated_commit` 去调 `submit_result`，用 `commit` 会验不到调度器生成的那个。

`intent_status` 取 §15 的 `pending | done | failed | unknown`。返回 `unknown`
时 Adapter **不得**重试，必须等 intent 对账（`intent_reconciled`）——
重试会在 canonical 里产生第二次 ref 写入。

---

## 5. 明确**不做**幂等的操作

| 操作 | 理由 |
|---|---|
| `heartbeat` | 语义就是覆盖 `lease_until`，重复调用天然收敛；加幂等键会让高频心跳塞满 `command_idempotency` |
| `probe` / `capabilities` / `list_ready_tasks` / `read_context` | 只读（`probe` 是否写 `capability_probes` 见 README Q-2） |
| `release` / `abandon` | 终态操作，第二次调用返回 `resource.not_found` 或 `lease.not_found` 即可，不需要首次结果 |
| `request_help` / `ask_human` | 用 `task_id + attempt_id` 去重即可；同一 attempt 内多次求助应该是**多条**消息，不是重放 |
| 所有 GET | 只读 |

`release` 不做幂等有个前提：它必须**先校验 `fencing_epoch`**。否则旧 epoch 的
release 会释放新 attempt 的 Lease —— §13"旧 epoch 的写入必须拒绝"覆盖这条。

---

## 6. 保留与清理

- `command_idempotency`、`claim_requests`、`task_claims` 都是 §8 定义的"只追加"事实
  记录，**不设 TTL**；
- 清理只能随任务归档一起做，且必须保留 `first_result_json` 的 hash，
  以便事后审计"这次重放返回的是不是首次结果"；
- `task_claims` 的行不能因为"这一项失败了"就删 —— 失败行是回放的依据（§11）。
- `side_effect_intents` 永久保留（§15 是副作用的唯一登记处，重启对账依赖它）。

---

## 7. 可执行的验收点（供 MVP-04 / MVP-08 直接写成测试）

| 编号 | 断言 | 对应 |
|---|---|---|
| IDEM-01 | 同 `claim_request_id` + 同 payload 调两次：第二次 HTTP 状态、body（除 `replayed`/`Idempotency-Replayed`）与第一次完全相同 | §11 |
| IDEM-02 | 同 `claim_request_id` + 不同 `task_ids`：返回 `idempotency.conflict`，且 `task_events` 中出现 `idempotency_conflict` | §11 |
| IDEM-03 | 批量 3 项、`partial_allowed=true`，其中 1 项已 owned：重复提交同 request_id，返回的 3 项中每项都是**各自的首次结果**，不是统一的当前状态 | §11、CORE-CLAIM-001 |
| IDEM-04 | 重放 claim 后 `tasks.attempt_no` 与 `tasks.current_epoch` 均未变化 | §4.1 第 5 条 |
| IDEM-05 | 两个连接并发提交同一 `claim_request_id`：都返回 200 且 body 相同，`task_attempts` 只有一行 | §2.1 |
| IDEM-06 | `submit_result` 容量满时返回 503，且 `tasks.owner` 与 `lease_until` 未变 | §26、README D-2 |
| IDEM-07 | 同 attempt 重复 `submit_result`（同 `completion_revision`）返回首次 receipt，`verification_receipts` 只有一行 | §16、§26 |
| IDEM-08 | 同 attempt 但**不同 commit** 的 submit 生成独立 receipt，不被旧记录挡回 | §16 |
| IDEM-09 | `changes_requested` 后新 attempt 的 submit 使用新 `attempt_id`，成功且不复用旧幂等结果 | §12 |
| IDEM-10 | 旧 `fencing_epoch` 的 `publish_task_branch` 返回 `fencing.epoch_stale`，且 `side_effect_intents` 无新行、canonical ref 未变 | §13、ADR-059 |
| IDEM-11 | `payload_hash` 由 Python 与 JS 两个实现对同一 body 算出相同值（含 `1.0` vs `1`、非 ASCII 字符串、嵌套数组三种边界） | §3 |
| IDEM-12 | 重放响应的 `first_created_seq` 等于首次响应的 `created_seq`，且重放不产生新 seq | §4.1 第 4 条 |
| IDEM-13 | 批量 3 项、其中 1 项被拒（如已 owned），事务提交后 `task_claims` 有 **3 行**（含被拒那行，带释放原因），不是 2 行 | §11、R10-fix-09 |
| IDEM-14 | 回放逐项结果走 `SELECT * FROM task_claims WHERE claim_request_id = ?`；把 `claim_requests.first_result_json` 人为置为 `NULL` 后，任务项级回放仍然正确 | §11、R10-fix-09 |
| IDEM-15 | `claim_requests` 表**没有** `task_id` 列；per-task 复合唯一键只存在于 `task_claims` | §8、R10-fix-09 |
| IDEM-16 | 迁移可重复执行后 `task_claims` 的 `unique(claim_request_id, task_id)` 仍被强制（插入重复行必须失败） | §23、DOC-SCHEMA-001 |

IDEM-11 是唯一一条不依赖 schema 就能跑的，建议 MVP-08 实现前先把它做成独立的
golden 测试，两个语言各一份 fixture，避免 Adapter 分裂。

IDEM-13 / 14 / 15 是 R10-fix-09 的直接产物，**建议 MVP-04 优先实现这三条** ——
它们正是"per-task 幂等落点放错表"这个已发生的缺陷的回归测试。本 prep 的第一版
就踩了同一坑（把复合键放在 `claim_requests`），说明这类错误在没有断言时极易复发。
