# MVP-08 统一错误码表（prep 草案）

真相源：R10 §10（状态机）、§11（Claim 与幂等）、§13（Lease/fencing）、§16（Verify）、
§17（安全）、§21（Merge Gate）、§37.7（事件 allowlist 88 项）

## 0. 使用规则

1. **错误码是封闭集合**。新增错误码必须同时给出 R10 引用；无法给出引用的，
   说明 R10 缺规范，走复审而不是在代码里加。
2. **`写入事件` 列只能填 §37.7 allowlist 内的名字**，或 `审计`（表示写审计记录但
   不写 `task_events`），或 `—`（R10 未定义，见 §5 缺口清单）。实现层禁止自造事件名
   （§37.7 封闭规则 5：不在 allowlist 中的事件必须拒绝并告警）。
3. **HTTP 状态码只用于传输层分类**，业务判据一律读 `error.code`。Adapter 不得
   用 `status == 409` 这类判断决定重试行为 —— 409 下既有可重试也有不可重试。
4. **`retryable=true` 的含义是"同一 attempt 内原样重试可能成功"**，不是"换参数重试"。
   `retryable=false` 的项必须由调度器状态机推进（例如等 Lease 过期、等人类决策），
   Adapter 自行重试只会刷事件。

## 1. 响应包络

所有非 2xx 响应统一为：

~~~json
{
  "error": {
    "code": "claim.attempt_budget_exhausted",
    "message": "attempt budget exhausted for task",
    "retryable": false,
    "task_id": "T-006",
    "attempt_id": "T-006.a2",
    "seq": 1043,
    "event_name": "attempt_budget_exhausted",
    "details": { "attempt_no": 3, "max_attempts": 3 }
  }
}
~~~

字段约束：

| 字段 | 必填 | 说明 |
|---|---|---|
| `code` | 是 | 本表定义的点分小写标识符，封闭集合 |
| `message` | 是 | **静态模板**，禁止拼接任何请求正文（见 §4 D-8） |
| `retryable` | 是 | 布尔，语义见 §0.4 |
| `task_id` / `attempt_id` | 否 | 能确定时必填，供 Adapter 关联 |
| `seq` | 否 | 若本次拒绝写了事件，填该事件的全局 seq |
| `event_name` | 否 | 若写了事件，填 allowlist 内的名字 |
| `details` | 否 | 结构化补充，**只允许标量与 ID**，不允许嵌套正文 |

---

## 2. 错误码表

### 2.1 请求层

| code | HTTP | 触发条件 | R10 | 写入事件 | retryable |
|---|---|---|---|---|---|
| `request.invalid` | 400 | JSON 解析失败、必填字段缺失、类型错误 | — | — | false |
| `request.schema_version_unsupported` | 422 | `schema_version` 高于服务端支持 | §8 | `—` | false |
| `resource.not_found` | 404 | `task_id` / `group_id` / `attempt_id` / `review_id` 不存在 | — | — | false |
| `internal.error` | 500 | 未分类异常 | — | `审计` | false |

### 2.2 身份与权限

| code | HTTP | 触发条件 | R10 | 写入事件 | retryable | test_id |
|---|---|---|---|---|---|---|
| `auth.identity_missing` | 401 | 缺 harness 身份或调用身份无法解析 | §17 | `审计` | false | — |
| `auth.token_pinned_forbidden` | 403 | Harness 尝试 pin/unpin token | §26"token pin/unpin 返回 403" | `审计` | false | DOC-AUTH-001 |
| `auth.not_localhost` | 403 | 请求源非 loopback | §5 | `审计` | false | — |
| `auth.host_header_rejected` | 400 | `Host`/`Origin` 非 localhost（DNS rebinding 防护） | §5 | `审计` | false | — |
| `secret.not_authorized` | 403 | `secret_refs` 未登记，或调用身份无最小权限 | §11 检查 9 | `审计` | false | DOC-AUTH-001 |
| `security.secret_scan_blocked` | 422 | 提交正文/消息命中 secret 扫描 | §17、§26 | `secret_scan_blocked`(O) | false | — |

`security.secret_scan_blocked` 的 `details` **只允许** `scan_id` 与 `rule_name`，
禁止返回命中片段、行号、上下文（D-8）。

### 2.3 Claim

| code | HTTP | 触发条件 | R10 | 写入事件 | retryable | test_id |
|---|---|---|---|---|---|---|
| `claim.task_not_ready` | 409 | 状态非 `ready`，且不构成 `changes_requested`/`plan_drift`/`impl_drift` 的合法返工 Claim | §11 检查 1 | `—` | false | CORE-CLAIM-001 |
| `claim.dependencies_not_merged` | 409 | 上游未 `merged`（`approved` **不算**满足） | §11 检查 2 | `—` | false | — |
| `claim.output_contract_unsatisfied` | 409 | `consumes` 的 name/type/schema_version 不闭合，或运行期上游 `task_outputs` 实际 hash 缺失/不兼容 | §11 检查 2 | `—` | false | DOC-PLAN-001 |
| `claim.already_owned` | 409 | 已有有效 owner + Lease | §11 检查 3 | `—` | false | **CORE-CLAIM-002** |
| `claim.capability_unsatisfied` | 403 | Harness 能力不满足任务 `capabilities` | §11 检查 5、§6 | `—` | false | — |
| `claim.quality_gate_unsatisfied` | 403 | 质量分/门控不满足；运行时自报能力不得单独用于高风险 Claim | §6、§11 检查 5 | `—` | false | — |
| `claim.concurrency_full` | 409 | 该 Harness 并发窗口已满（`max_parallel_tasks`） | §11 检查 7 | `—` | true | — |
| `claim.plan_drift` | 409 | 任务绑定的 plan hash 与当前不符 | §11 检查 8 | `plan_hash_changed`(M) | false | — |
| `claim.impl_drift` | 409 | 实现侧 hash 漂移 | §10 | `impl_hash_changed`(M) | false | — |
| `claim.attempt_budget_exhausted` | 409 | `attempt_no >= max_attempts` 且 `budget_override_by IS NULL` | §11 R8 SQL guard、§8 | `attempt_budget_exhausted`(M) | false | CORE-CLAIM-001 |
| `claim.base_commit_not_ancestor` | 409 | 已有任务的 `base_commit` 为空，或不是当前 integration HEAD 的显式祖先 | §11 | `—` | false | — |
| `claim.base_commit_immutable` | 409 | 请求试图为已 Claim 任务改写 `base_commit` | §11、§13 | `审计` | false | — |
| `claim.upstream_invalidated` | 409 | 上游被 revert，`upstream_invalidated_seq` 已写，下游 Claim 被冻结 | §21、§26 | `upstream_invalidated`(O) 已存在 | false | MERGE-REVERT-002 |
| `idempotency.conflict` | 409 | 同一 `claim_request_id`/`idempotency_key`，但 `task_ids`、参数或 `payload_hash` 不一致 | §11、§12 | `idempotency_conflict`(O) | false | CORE-CLAIM-001 |

**不是错误的两个 Claim 结果**（见 README D-1）：

| outcome | HTTP | 条件 | 写入事件 |
|---|---|---|---|
| `queued` | 200 | worktree/端口/DB/GPU/浏览器 profile 等资源预占失败 | `claim_deferred_resource`(M) |
| `claimed` | 200 | 全部检查通过且资源预占成功 | `claim`(M) |

`queued` 响应必须带 `queue_reason`，且**不含** `owner` / `attempt_id` / `lease_until`
（§11："queued 不记录以下 owner 字段"）。

### 2.4 Lease、Heartbeat、fencing

| code | HTTP | 触发条件 | R10 | 写入事件 | retryable | test_id |
|---|---|---|---|---|---|---|
| `lease.not_found` | 404 | `lease_token` 无对应有效 Lease | §13 | `—` | false | — |
| `lease.wrong_owner` | 403 | 调用身份不是 Lease 持有者 | §13 | `审计` | false | CORE-LEASE-001 |
| `lease.expired` | 409 | `lease_until` 已过；续期被拒即视为失联 | §13"续期被拒：立即视为失联" | `lease_expire`(M) 由巡检器写，API 侧 `审计` | false | CORE-LEASE-001 |
| `fencing.epoch_stale` | 409 | 请求携带的 `fencing_epoch` 小于 `tasks.current_epoch` | §13、§21 | `审计`（拒绝审计事件） | false | **CORE-FENCE-001** |
| `fencing.epoch_missing` | 400 | 写操作缺 `fencing_epoch`（`heartbeat`/`publish_task_branch` 必填） | §7 签名 | `—` | false | CORE-FENCE-001 |

`fencing.epoch_stale` 适用于 heartbeat、submit_result、publish_task_branch、
review.submit 全部写路径 —— §13："旧 epoch 的写入、push 和 merge 必须拒绝"。

### 2.5 Submit 与 Verify

| code | HTTP | 触发条件 | R10 | 写入事件 | retryable | test_id |
|---|---|---|---|---|---|---|
| `submit.revision_stale` | 409 | `completion_revision` 已被更高 revision 取代 | §12 幂等键 | `—` | false | — |
| `submit.attempt_mismatch` | 409 | `attempt_id` 不是 `tasks.current_attempt_id` | §8 | `审计` | false | — |
| `submit.verify_capacity_full` | **503** | `verify_jobs` 达到容量上限（§26 提到 32） | §26、§21 背压 | `submit_rejected_verify_capacity`(M) | **true** | CORE-* |
| `submit.completion_incomplete` | 422 | 缺精确 commit / 修改文件 / 测试命令与结果 / 已知问题 / 交接建议 / task_id / attempt_id / fencing epoch | §16"完成提交必须包括" | `—` | false | — |
| `verify.receipt_commit_mismatch` | 409 | receipt 的 commit 与待合并 commit 不一致 | §16、§21 | `—` | false | VERIFY-RECEIPT-001 |
| `verify.differential_failed` | 200 | baseline/head differential 不成立 → `changes_requested` | §16 步骤 4 | `verify_code_failed`(M) | false | VERIFY-DIFF-001 |
| `verify.env_failed` | 200 | overlay 创建失败、工具链缺失、secret 注入失败、网络白名单阻断 | §10 自环 | `verify_env_failed`(M) | true | VERIFY-DIFF-001 |
| `verify.execution_timeout` | 200 | verify 执行超时 → `escalated` | §16 | `verify_execution_timeout`(M) | false | — |
| `verify.queue_timeout` | 200 | verify 排队超 SLA（30 分钟） → `escalated` | §16 | `verify_queue_timeout`(M) | false | — |
| `verify.resource_blocked` | 423 | verify 所需资源被占，排队中 | §16 | `verify_resource_blocked`(O) | true | — |
| `verify.differential_needs_human` | 200 | `differential: not_applicable`，需人类确认 | §16 步骤 5、§10 | `differential_not_applicable`(M) | false | VERIFY-DIFF-001 |

`submit.verify_capacity_full` 是本表最容易实现错的一条：**503 且不得释放 owner Lease**
（README D-2）。响应必须带 `Retry-After`。

`verify.*` 那五条 HTTP 是 200，因为它们是状态机的**正常迁移**，不是调用失败。
把它们做成 4xx/5xx 会让 Adapter 误判为需要重发 submit。

### 2.6 Publish（Push Proxy）

| code | HTTP | 触发条件 | R10 | 写入事件 | retryable | test_id |
|---|---|---|---|---|---|---|
| `publish.branch_not_allowed` | 403 | 目标分支不在任务分支白名单，或试图写 `integration/main` | §13、§14 分支命名 | `审计` | false | **GIT-ACL-001** |
| `publish.branch_name_invalid` | 422 | 不符合 `agent/<group-short>/<harness>/<task-id>/<attempt-id>`；help 分支须为 `help-<n>` | §14、R10-09 | `—` | false | GIT-ACL-001 |
| `publish.commit_unreachable` | 409 | commit 在 agent gitdir 中不可达，或父链校验失败 | ADR-059 步骤 3 | `审计` | false | GIT-ACL-001 |
| `publish.alternates_invalid` | 403 | `.git/objects/info/alternates` 指向非本项目 canonical objects | ADR-059 步骤 1 | `审计` | false | **GIT-ACL-002** |
| `publish.head_moved` | 409 | `approved` 后任务分支头前移 | §21、§13 分支冻结 | `reopen_for_changes`(M) | false | MERGE-EXACT-001 |
| `publish.branch_frozen` | 403 | 分支处于 `approved` 冻结状态，仅 `human.adjudicate` 或 revert recovery 可解冻 | §13 | `审计` | false | — |
| `publish.intent_duplicate` | 200 | 同 `idempotency_key` 的 `side_effect_intents(type=push)` 已存在 → 返回首次结果 | §15、§12 | 重放，不新写 | — | — |
| `publish.pack_transfer_failed` | 502 | 跨账户 pack/bundle 传输失败（Defender 锁文件等） | ADR-059、GIT-PACK-001 | `审计`，intent 置 `unknown` | true | GIT-PACK-001 |

### 2.7 Review

| code | HTTP | 触发条件 | R10 | 写入事件 | retryable | test_id |
|---|---|---|---|---|---|---|
| `review.self_review_forbidden` | 403 | Reviewer 是该 attempt 的 owner | §21、§26 | `审计` | false | — |
| `review.same_harness_forbidden` | 403 | 同 harness 自审 | §26 | `审计` | false | — |
| `review.same_provider_needs_human` | 409 | 同 provider 的两个 Reviewer，需额外人类确认 | §21 | `—`（**缺口，见 §5**） | false | — |
| `review.commit_mismatch` | 409 | `reviewed_commit` ≠ 待合并 commit / receipt.commit | §21 | `—` | false | MERGE-EXACT-001 |
| `review.round_exhausted` | 409 | `review_round >= max_review_rounds`（默认 2） | §13、§10 | `review_round_exhausted`(M) | false | — |
| `review.lease_expired` | 409 | review Lease（20 分钟）到期 | §13、§16 | `review_lease_expire`(M) | false | — |
| `review.verdict_not_allowed` | 422 | `verdict` 不在 allowlist（`approve`/`request_changes`） | §8"review verdict 使用 allowlist" | `—` | false | — |
| `review.requires_verify_receipt` | 409 | 尚无通过的 receipt，Review 不得消费 failed/timeout/不匹配 commit 的 receipt | §16"Verify 与 Review 串行" | `—` | false | VERIFY-RECEIPT-001 |
| `review.judge_cannot_approve` | 422 | judge 类型 acceptance 试图单独产生 `approved` | §16 judge 约束 | `—` | false | — |

### 2.8 Merge Gate

| code | HTTP | 触发条件 | R10 | 写入事件 | retryable | test_id |
|---|---|---|---|---|---|---|
| `merge.receipt_commit_mismatch` | 409 | `receipt.commit` ≠ `review.reviewed_commit` ≠ 待合并分支 head | §21 | `—` | false | **MERGE-EXACT-001** |
| `merge.epoch_mismatch` | 409 | `receipt.fencing_epoch` ≠ `tasks.current_epoch` | §21 | `审计` | false | CORE-FENCE-001 |
| `merge.base_commit_mismatch` | 409 | `receipt.base_commit` ≠ `tasks.base_commit` | §21、§13 | `—` | false | MERGE-EXACT-001 |
| `merge.receipt_missing` | 409 | 无 append-only receipt，或 receipt 被 UPDATE/DELETE 痕迹 | §16、§21 | `审计` | false | VERIFY-RECEIPT-001 |
| `merge.required_reviews_unmet` | 409 | `required_reviews` / `review_policy: cross_provider` 未满足 | §21 | `—` | false | — |
| `merge.unresolved_findings` | 409 | 存在高优先级未解决意见 | §21 | `—` | false | — |
| `merge.secret_scan_blocking` | 409 | secret 扫描有阻断项 | §21、§17 | `secret_scan_blocked`(O) 已写 | false | — |
| `merge.integration_red` | 409 | `integration_health=red`，stop-the-line（hotfix 也不豁免） | §21 | `—`（**缺口，见 §5**） | false | MERGE-REVERT-001 |
| `merge.queue_full` | 503 | Merge Queue 背压，带 `Retry-After` | §21 | `—` | true | — |
| `merge.conflict` | 409 | 可自动消解的冲突 | §10、§21 | `merge_conflict`(M) | true | MERGE-EXACT-001 |
| `merge.conflict_needs_owner` | 409 | 需 owner 重做 | §10、§21 | `merge_conflict_needs_owner`(M) | false | MERGE-EXACT-001 |
| `merge.post_commit_failure` | 500 | 合并后集成测试失败，走 revert recovery | §21 | `merge_post_commit_failure`(M) | false | **MERGE-REVERT-001/002** |
| `merge.revert_assertion_failed` | 500 | `tree(revert_commit) != tree(first_parent(failed_merge_commit))`，或历史夹带提交 | §21 | `merge_post_commit_failure_escalated`(M) | false | MERGE-REVERT-001 |
| `merge.lease_expired` | 409 | merge Lease（30 分钟）到期 | §13、§16 | `merge_lease_expire`(M) | false | — |
| `merge.bypass_forbidden` | 403 | 试图人工绕过 gate；绕过请求必须进 `awaiting_human` | §21 | `human.question`(M) | false | — |

### 2.9 状态机与 Plan 校验

| code | HTTP | 触发条件 | R10 | 写入事件 | retryable | test_id |
|---|---|---|---|---|---|---|
| `state.transition_undefined` | 409 | `(current, event)` 不在 §10 迁移表内 | §10、清单 H-1 | `—` + **告警** | false | DOC-STATE-001 |
| `state.event_not_in_allowlist` | 422 | 事件名不在 88 项 M/O/R 集合内 | §37.7 封闭规则 5 | `—` + **告警** | false | DOC-STATE-001 |
| `state.observation_event_cannot_drive` | 422 | 用 O/R 集合事件尝试驱动迁移 | §37.7 封闭规则 6 | `—` | false | DOC-STATE-001 |
| `plan.cycle_detected` | 422 | 任务依赖 DAG 成环 | §26、清单 MVP-03 | `—` | false | **DOC-PLAN-001** |
| `plan.invalid_output_ref` | 422 | `consumes` 引用不存在的 task/output | §26"上游 output 未声明" | `—` | false | DOC-PLAN-001 |
| `plan.oversized` | 422 | 计划超规模 | §26 | `—` | false | DOC-PLAN-001 |
| `plan.evidence_unsatisfiable` | 422 | `acceptance.evidence_source` 不可满足 | §26 | `—` | false | DOC-PLAN-001 |
| `plan.verify_cmd_not_allowed` | 422 | verify 命令根不在仓库侧人类维护的 policy allowlist | §16 | `—` | false | DOC-PLAN-001 |
| `plan.judge_as_gate_forbidden` | 422 | 门禁项使用 `judge`（只允许 `cmd` 或 `human`） | §16 | `—` | false | — |
| `plan.hash_mismatch` | 409 | `plan_hash` 与已提交 snapshot 不符 | §21 `planner.validate_plan` | `plan_hash_changed`(M) | false | — |

### 2.10 群聊与上下文

| code | HTTP | 触发条件 | R10 | 写入事件 | retryable |
|---|---|---|---|---|---|
| `message.mention_unresolvable` | 422 | `@mention` 指向不存在的 harness/人类 | §19 | `—` | false |
| `message.secret_scan_blocked` | 422 | 消息正文命中 secret 扫描，**正文不落库** | §17、§26 | `secret_scan_blocked`(O) | false |
| `context.budget_exhausted` | 200 | 剩余 budget < 500 tokens，只返回计数与 `fetch_detail(seq_range)` | §19 | `—` | — |
| `context.seq_gap` | 500 | 服务端检测到 seq 空洞 | §15"禁止制造空洞" | `审计` + 告警 | false |

`context.budget_exhausted` 是 200：§19 规定的降级返回，不是错误。

---

## 3. HTTP 状态码使用约定

| 状态 | 用途 | 是否可原样重试 |
|---|---|---|
| 200 | 成功；以及**状态机正常迁移**（含 queued / verify 失败 / 预算降级返回） | — |
| 400 | 请求格式错误 | 否 |
| 401 | 身份缺失 | 否 |
| 403 | 身份存在但无权限（能力、ACL、secret、自审） | 否 |
| 404 | 资源不存在 | 否 |
| 409 | 协调冲突：状态、Lease、epoch、commit、gate 断言 | **读 `retryable`** |
| 422 | 语义校验失败：allowlist、plan linter、字段封闭集合 | 否 |
| 423 | 资源被占，排队中 | 是（退避） |
| 500 | 服务端错误，含 merge post-commit failure | 否 |
| 502 | 外部/跨进程传输失败（pack transfer） | 是 |
| 503 | 容量与背压，**必带 `Retry-After`** | 是 |

**禁止**用 409 表达"资源不存在"，也**禁止**用 500 表达业务拒绝 —— 前者会让
Adapter 的重试逻辑把 not_found 当竞争失败无限重试，后者会让监控把正常门禁
统计成服务故障。

---

## 4. 与 D-8 相关的硬约束

以下字段**任何情况下都不得**出现在错误响应、日志或事件 payload 中：

- secret 值、secret 命中片段、命中行号；
- `completion` 正文、`findings` 正文、群聊消息正文（命中扫描时）；
- 环境变量内容、`.git/config` 内容；
- 完整 verify 日志（只允许 `log_hash`，§16 receipt 记录"日志 hash"）。

违反这一条会直接破坏 §17 的信任边界，并且让 §26 的
"secret 扫描命中时正文不进入 append-only 事件或消息"无法通过测试。

---

## 5. R10 规范缺口（需要复审裁定，不得在实现里补）

本表编写过程中发现 4 处"API 需要拒绝，但 R10 没有定义对应事件名"的情况。
按清单 H-1，任何新增事件必须先改 R10 正文并升 `schema_version`，**不能在代码里加**。
所以这 4 处目前只能写 `审计`，登记如下：

| 缺口 | 场景 | 现处置 | 建议 |
|---|---|---|---|
| G-1 | Claim 被拒（非幂等冲突）时是否写 `task_events` | §11 只规定了 `idempotency_conflict` 要写事件，其余拒绝路径无规定 | 明确"拒绝只写审计，不写 task_events"，否则失败重试会污染事件流并使 seq 膨胀 |
| G-2 | 旧 `fencing_epoch` 写入被拒 | §13 说"必须产生拒绝审计事件"，但 88 项 allowlist 内没有对应名字 | 要么归入 `审计` 类（不进 task_events），要么在 §37.7 的 O 集合新增一项并升 schema_version |
| G-3 | 同 provider 双 Reviewer 需人类确认 | §21 要求"额外人类确认"，但无事件名；`human.question` 语义是 Harness 主动 ask_human | 复用 `human.question` 会混淆发起方。建议新增 O 集合项或明确复用口径 |
| G-4 | `integration_health=red` 时 stop-the-line 拦截新 merge | §21 定义了集成健康状态机的 5 条边，但没有"被拦截的 merge 请求"的事件 | 集成健康状态机与任务状态机独立，建议明确它有自己的事件命名空间，而不是塞进 88 项 |

**G-2 和 G-4 影响 CORE-FENCE-001 与 MERGE-REVERT-001 的可断言性**：如果拒绝不产生
可查询的事件，测试就只能断言 HTTP 状态码，而清单 H-4 明确禁止用"功能可用"作为
完成标准。建议在 MVP-01 的 `tools/docstate` 里加一条断言：§26 测试矩阵中每个
test_id 都能追溯到至少一个 allowlist 事件或一个明确的"无事件"裁定。
