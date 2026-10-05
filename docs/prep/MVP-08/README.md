# MVP-08 prep：REST API 契约草案

状态：`doing`（prep 不进主分支；产出位于 `.worktrees/qoder/docs/prep/MVP-08/`，
由协调者按 `docs/prep/README.md` 的规则统一取用）
真相源：`docs/spec/0.3-R10.md` §7（Adapter 接口）、§21（Planner/Review/Git API）、
§8（canonical schema）、§11（Claim 与幂等，含 R10-fix-09）、§12（命令幂等键）、
§13（Lease/fencing）、§15（事件与 seq）、§19（群聊与 read_context）、§26（检查 ID）、
§37.7（事件 allowlist）
产出文件：

| 文件 | 内容 |
|---|---|
| `openapi.yaml` | OpenAPI 3.1 契约草案，全部端点 + 请求/响应 schema |
| `error-codes.md` | 统一错误码表：code → HTTP → 触发条件 → R10 引用 → 是否写事件 → test_id |
| `idempotency.md` | 幂等键构成、双层幂等、重放响应约定、批量 Claim 语义 |
| `check_contract.py` | 机械校验脚本，见 §6；退出码 0 = 契约是 R10 的忠实投影 |
| `mutation_test.py` | 对 `check_contract.py` 的变异测试，见 §6.2；证明每条断言都不是空跑 |
| `README.md` | 本文件：命名对齐矩阵、关键设计决策、未决问题、对 MVP-02 的 schema 要求 |

**本 prep 不含实现代码**，只含契约。契约里所有状态名、事件名、字段名都从 R10 抄录，
没有自造（H-1）。

## 0. 落地时修正了什么（2026-10-05，Qoder）

这份 prep 的第一版是对着 `w-f/outputs` 下的**未修订 R10** 写的；本仓库的
`docs/spec/0.3-R10.md` 已被 `MVP-02-amend` 按 **R10-fix-09** 修订过。落地时逐条比对，
确认偏差**只集中在 §8/§11 的 Claim 幂等落点**，其余（§10 状态集合、§37.7 allowlist、
§7/§21 动词表）两版逐字一致 —— 后者由 `check_contract.py` 的 A/C/D/E/F 组机械确认。

| 项 | 第一版（旧 R10） | 本版（R10-fix-09 口径） |
|---|---|---|
| per-task 唯一性 | `claim_requests.unique(claim_request_id, task_id)` | **`task_claims.unique(claim_request_id, task_id)`** |
| per-task 首次结果回放 | 从 `first_result_json` 反解 | `SELECT * FROM task_claims WHERE claim_request_id = ?`，**不从 JSON 里反解** |
| 失败项 | 未涉及 | **必须留 `task_claims` 行并带释放原因** |
| 请求级首次结果 | `claim_requests.first_result_json` | 不变 |

改动落在 `idempotency.md`（§1.1 表、§2.1、§2.2、§4.1、§6、§7 新增 IDEM-13～16）、
`README.md`（本文件 §0、§4 Q-4、§5、§6）、`check_contract.py`（新增 J 组回归断言）、
`mutation_test.py`（新增一个对应变异）。

**并由此发现一个新的规范缺口 Q-6**（`task_claims` 没有 §8 最小定义行，而
R10-fix-09 把权威唯一键放在了它上面，且承载它的 §37.6 单元格恰好落在 `schema-keys`
断言组的跳过名单里）。见 §4 Q-6。

**Q-6 已于同日被 R10-fix-10 修复**（WorkBuddy，commit `32f4e45`）：§8 新增
`task_claims` 行、§37.6 的合并单元格拆开、`tools/docstate` 的 `schema-keys` 组扩围
44 行。本条记录保留，用于说明"一个按其自身规则查不出缺陷的断言组"是如何被发现的。
随之本 prep 里那条**按设计会转红的哨兵断言已删除**，改为对修复结果的正向断言
（J 组，`check_contract.py`）。

**教训**：这次偏差是靠人工逐行比对 + 新增断言发现的，**不是**靠第一版那 9 组断言 ——
它们在两版 R10 上都全绿。断言覆盖不到的地方，"0 失败"不含任何信息。

---

## 1. 关键发现：对齐目标不止 §7

清单 MVP-08 的绑定提示说"路径命名应与 R10 §7 Adapter 接口对齐，否则 MVP-10 的 Adapter
要做一层无意义的转换"。**这条只覆盖了一半。**

R10 实际定义了**三个**互相独立的动词族：

| 族 | 出处 | 动词数 | 调用方 |
|---|---|---|---|
| Adapter 拉取接口 | §7 | 16 | Harness（开发角色） |
| `planner.*` / `review.*` / `git.*` | §21 | 6 | Planner、Reviewer、Push Proxy |
| 读取与管理面 | §6/§8/§19/§22 | — | 人类、前端、巡检器 |

§7 里**没有** review 相关动词 —— `review.claim` 和 `review.submit` 在 §21 才定义。
如果 MVP-08 只按 §7 对齐，Reviewer Harness（MVP-07、MVP-10b 都要用）就没有接口可调，
MVP-10 的 Adapter 反而要为 review 现编一层转换，正好是绑定提示想避免的事。

**处置**：契约同时按 §7 和 §21 对齐，两组动词的 path 段一律使用 R10 原文拼写。

---

## 2. 命名空间划分

三个命名空间，按调用方身份而不是按资源划分。理由是 §17 的权限边界本来就按身份切
（`agent-harness-<id>` / `agent-verify` / `agent-scheduler` / 人类），path 前缀与身份
对齐后，ACL 可以按前缀挂载，不需要逐端点配。

~~~text
/api/v1/adapter/*    §7 的 16 个动词，Harness 开发角色专用
/api/v1/review/*     §21 的 review.claim / review.submit，Reviewer 角色专用
/api/v1/plan/*       §21 的 planner.*，Planner 角色专用
/api/v1/*            管理面与读取面：projects / groups / tasks / events / messages / merges
~~~

`git.publish_task_branch` 归入 `/api/v1/adapter/publish_task_branch`，因为 §13/ADR-059
明确"Harness 只调用 REST `publish_task_branch`"—— 它的调用方是 Harness，不是 Merge Worker。

### 2.1 §7 动词 → path 对齐矩阵

path 段**逐字使用 §7 的拼写**（含 `list_ready_tasks` 这种较长的形式、含 snake_case）。
放弃 REST 美观换取 1:1 可映射：MVP-10 的 Adapter 实现体就是把 §7 的函数签名直接
POST 到同名 path，函数名 == path 尾段，零转换表。

| §7 签名 | Method + Path | 幂等键（§12） |
|---|---|---|
| `probe()` | `POST /api/v1/adapter/probe` | 非幂等（只读探测，可重复） |
| `capabilities()` | `GET /api/v1/adapter/capabilities` | 只读 |
| `list_ready_tasks(visibility)` | `GET /api/v1/adapter/list_ready_tasks?visibility=authorized\|redacted` | 只读 |
| `claim(claim_request_id, task_ids, payload_hash)` | `POST /api/v1/adapter/claim` | `claim_request_id` + `task_id`（按项） |
| `heartbeat(task_id, lease_token, fencing_epoch)` | `POST /api/v1/adapter/heartbeat` | 天然幂等（覆盖 lease_until） |
| `read_context(task_id, since_seq)` | `GET /api/v1/adapter/context/{task_id}?since_seq=` | 只读 |
| `submit_result(task_id, attempt_id, completion_revision, completion)` | `POST /api/v1/adapter/submit_result` | `task_id + attempt_id + completion_revision` |
| `request_help(task_id, reason)` | `POST /api/v1/adapter/request_help` | `task_id + attempt_id` |
| `report_blocked(task_id, blocker_ref)` | `POST /api/v1/adapter/report_blocked` | `task_id + attempt_id + blocker_ref` |
| `accept_help(task_id, help_id)` | `POST /api/v1/adapter/accept_help` | `help_id` |
| `reject_help(task_id, help_id)` | `POST /api/v1/adapter/reject_help` | `help_id` |
| `split_task(task_id, child_specs, idempotency_key)` | `POST /api/v1/adapter/split_task` | `idempotency_key` |
| `ask_human(task_id, question)` | `POST /api/v1/adapter/ask_human` | `task_id + attempt_id` |
| `abandon(task_id, reason)` | `POST /api/v1/adapter/abandon` | `task_id + attempt_id` |
| `release(task_id)` | `POST /api/v1/adapter/release` | `task_id + attempt_id` |
| `publish_task_branch(task_id, attempt_id, branch, commit, fencing_epoch, idempotency_key)` | `POST /api/v1/adapter/publish_task_branch` | `task_id + attempt_id + commit + fencing_epoch + idempotency_key` |

### 2.2 §21 动词 → path

| §21 签名 | Method + Path |
|---|---|
| `planner.create_plan(plan_id, plan_snapshot, idempotency_key)` | `POST /api/v1/plan/create_plan` |
| `planner.validate_plan(plan_id, plan_hash)` | `POST /api/v1/plan/validate_plan` |
| `planner.submit_plan(plan_id, plan_hash)` | `POST /api/v1/plan/submit_plan` |
| `review.claim(review_id, attempt_id, reviewer_harness_id)` | `POST /api/v1/review/claim` |
| `review.submit(review_id, reviewed_commit, verdict, findings, evidence_hash, review_revision, idempotency_key)` | `POST /api/v1/review/submit` |
| `git.publish_task_branch(...)` | 见 §2.1，归 adapter |

`request_changes` 在清单里列为 MVP-08 交付项，但 §7/§21 都没有对应的独立动词 ——
它是 `review.submit(verdict=request_changes)` 的一个取值（§37.7 的 M 集合里
`request_changes` 是事件名）。**契约不新开 `/request_changes` 端点**，走 `review.submit`。

---

## 3. 关键设计决策

### D-1 `queued` 不是错误

§11：资源预占失败时"只进入 `queued`，不写 owner、attempt、Lease 或开发副作用"，
对应事件 `claim_deferred_resource`（§37.7 M 集合）。

所以 `POST /adapter/claim` 在资源不足时返回 **HTTP 200**，body 里
`items[].outcome = "queued"`，带 `queue_reason`。返回 4xx/5xx 会让 Harness Adapter
把它当失败重试，正好破坏 §11 的"queued 不记录 owner 字段"语义。

同理 `verify_env_failed` 的自环（§10：`verifying | verify_env_failed | verifying`）
在 API 层是 200 + `outcome="retrying"`，不是 500。

### D-2 `submit_result` 容量不足返回 503 且**不释放 Lease**

§26 明确要求："verify_jobs 满 32 时 `submit_rejected_verify_capacity` **保持 owner Lease**，
容量恢复后可重试"。所以：

- HTTP `503`，带 `Retry-After`；
- error code `submit.verify_capacity_full`；
- **不得**触发 §13 的"submit_result 成功后立即释放开发 Harness 的 owner Lease"
  —— 因为 submit 没成功；
- 写 `submit_rejected_verify_capacity` 事件（M 集合）。

如果按常规把 503 当"服务端错误、客户端可安全重试"，Adapter 可能同时放弃 Lease，
任务会掉回 ready 被别人抢走，已完成的开发工作白费。这条要在 Adapter 契约里写死。

### D-3 幂等键放 body，不放 header

§7 的签名把 `claim_request_id` / `idempotency_key` 作为**函数参数**。放 header 会让
Adapter 出现"参数在 body、键在 header"的分裂，且 `payload_hash` 的计算范围变得含糊。
统一放 body，`payload_hash` 覆盖 body 中除 `claim_request_id`/`idempotency_key`/
`payload_hash` 三者自身以外的全部字段（详见 `idempotency.md`）。

**不接受** `Idempotency-Key` header 作为别名。两个入口 = 两份真相源。

### D-4 所有成功响应都带 `seq`

§15："事件排序使用全局 seq，时间戳仅用于展示"。所以每个写操作的响应都带
`created_seq`，并在 `X-Event-Seq` header 重复一份，供客户端廉价轮询/对齐。
前端和 Adapter 的增量拉取一律用 `since_seq`，不用 `updated_at`。

### D-5 `since_seq` 是**排他下界**，且禁止空洞

`GET /api/v1/events?since_seq=N` 返回 `seq > N` 的事件，升序，带 `next_seq` 与
`has_more`。§15 规定"恢复时以 seq 去重，禁止制造空洞或重复事实事件"，所以：

- 客户端可以断言返回序列连续（`seq[i+1] == seq[i] + 1`），除非中间有被 `redact`
  事件遮蔽的项 —— 此时返回占位项而不是跳过，保持连续性可断言；
- WebSocket 断线重连用同一个 `since_seq` 语义（§26 "WebSocket 断线补 seq"）。

### D-6 只绑定 localhost，且不设 CORS

§5：v1 REST 与 WebSocket 默认只绑定 localhost。契约层加两条：

- 服务端绑定 `127.0.0.1`，**不**绑 `0.0.0.0`，也不绑 `::`（IPv6 通配会绕过
  "只绑 localhost"的意图）；同时监听 `::1` 需显式开关；
- 不返回任何 `Access-Control-Allow-Origin`。浏览器里的前端页面（MVP-09）与 API 同源
  由 Vite dev proxy 解决，不靠 CORS。否则任意网页都能驱动本地调度器。
- 校验 `Host` / `Origin` header：拒绝非 localhost 的 `Host`，防 DNS rebinding。

### D-7 状态与事件名是**枚举**，不是自由字符串

`openapi.yaml` 里 `TaskStatus` 枚举直接抄 §10 的 23 个正式状态（清单 H-1 说 24 个，
见下方未决问题 Q-1），`EventName` 枚举抄 §37.7 的 88 项。契约层就锁死，
使 `state.event_not_in_allowlist` / `state.transition_undefined` 能在框架校验阶段
（422）而不是业务逻辑深处被拒。

### D-8 错误响应体永不回显请求正文

§17 / §26："secret 扫描命中时正文不进入 append-only 事件或消息"。所以
`security.secret_scan_blocked` 的 error body **不得**包含命中片段、行号或原文摘要，
只返回 `secret_scan_id` 与规则名。同理所有错误的 `message` 字段必须是静态模板，
不拼接用户输入 —— 否则 secret 会经错误响应回流到日志和前端。

---

## 4. 未决问题（需要 R10 复审或 MVP-01/MVP-02 裁定）

### Q-1 §10 的状态数是 23 还是 24？

清单 H-1 写"R10 §10 的 **24** 个正式状态"。实际逐行数 §10 的"正式状态"代码块
（R10 正文 378–402 行）得到 **23** 个：

~~~text
draft, draft_rejected, ready_waiting_deps, ready, queued, verifying, claimed,
in_progress, needs_help, blocked, changes_requested, plan_drift, impl_drift,
awaiting_review, approved, merging, stale, reclaiming, abandoned, cancelled,
escalated, awaiting_human, merged
~~~

差 1 个。可能原因：`child_created` 被 §37.7 封闭规则 4 称为"split 专用伪状态"，
若计入则为 24。**这不是小事**：`DOC-STATE-001` 要断言状态集合大小，而 §37.7 的
方法论结论恰恰是"allowlist 的任何数字都不得手写，必须由脚本从集合实际元素算出"。

**建议**：MVP-01 移植 `tools/docstate` 时把状态集大小也改成脚本算出，然后回写
R10 与清单 H-1 的数字。在裁定前，契约的 `TaskStatus` 枚举按**实际列出的 23 个**写，
并把 `child_created` 单独标为 `x-pseudo-state: true`，不混入 `tasks.status` 的取值域。

### Q-2 `probe()` 是幂等的吗

§7 把 `probe()` 列为第一个动词，但 §6 说"能力来自 probe 结果和人类维护的注册表"，
§8 有 `capability_probes` 表。probe 每次调用应该写一条 `capability_probes` 记录
（因此**不是**只读），还是只在能力变化时写？契约草案暂按"每次都写、无幂等键、
不产生事件"处理，需要 MVP-02 确认 `capability_probes` 的追加语义。

### Q-3 Reviewer Lease 的 `lease_token` 从哪来

§13："awaiting_review 由 review worker 获得独立 review Lease"。§21 的
`review.claim(review_id, attempt_id, reviewer_harness_id)` 签名里**没有**
`review_lease_token` 出参的说明，但 `review.submit` 需要证明自己持有有效 Lease。
契约草案让 `review.claim` 的响应返回 `review_lease_token` + `lease_until` +
`fencing_epoch`，`review.submit` 必须回带。**需要 R10 确认这个字段名**，
否则 MVP-07 和 MVP-08 会各起一个名字（就是 R9 那类"两份真相源"）。

### Q-4 批量 Claim 的 `partial_allowed=false` 时，失败项如何呈现

§11："批量 Claim 默认 all-or-nothing。显式允许部分成功时，每项独立返回结果并写事件。"
all-or-nothing 失败时，整个请求回滚 → 应该返回**单个** error（HTTP 4xx）还是
`items[]` 数组里每项都带同一个 error？契约草案选后者（结构统一，Adapter 不用分叉
解析逻辑），但 HTTP 状态取首个失败项的状态码。

**R10-fix-09 已部分回答**：§11 新增"批量 Claim 的每个任务项都必须留下一行
`task_claims`，失败项同样留行并带释放原因"—— 既然失败项也要逐项留行，响应做成
逐项结构就不是可选的美化了。剩下未决的只有 HTTP 状态码取哪个。

### Q-5 `visibility=redacted` 的裁剪规则在哪定义

§7 有 `list_ready_tasks(visibility=authorized|redacted)`，但正文没有给出 `redacted`
具体裁剪哪些字段。契约草案暂按"裁剪 `secret_refs`、`acceptance.command`、
`exclusive_paths` 的具体路径、群聊正文"处理，标为 TODO，需要 §17 补口径。

### Q-6 `task_claims` 没有 §8 最小定义行，而 R10-fix-09 把权威唯一键放在了它上面
> **状态：已修复（2026-10-05，R10-fix-10，commit `32f4e45`）。** 以下内容保留为
> 问题记录。修复内容：§8 新增 `| task_claims | claim_id; unique(claim_request_id,
> task_id) | ... |` 行；§37.6 的 `task_claims/task_attempts` 合并单元格拆成两行，
> 使 `schema-keys` 组不再跳过；`tools/docstate` 的 `schema-keys` 组扩围 44 行。
> 本 prep 的哨兵断言随之删除，J 组改为对修复结果的正向断言。
>
> 修复方式本身值得记一笔：**列名取自 `001_initial.sql` 的既成实现，而不是反过来
> 让实现去凑规范** —— 这正是 Q-6 里建议的顺序。

**这是本次落地时新发现的，比 Q-1～Q-5 都更需要裁定。**

R10-fix-09 的两条落点是：

1. §8 `claim_requests` 行的复合唯一键**删除**，改在「写入方与保留」注明
   "per-task 唯一性与 per-task 首次结果由 `task_claims` 的
   `unique(claim_request_id, task_id)` 承担，见 §11"；
2. §11 新增两层幂等职责划分段。

但 §8 的**最小定义表**（`| 表 | 主键/唯一键 | 关键字段 | 写入方与保留 |` 那张）实际
只有 13 行：`tasks`、`claim_requests`、`task_attempts`、`task_outputs`、
`test_case_results`、`secrets`、`verify_jobs`、`verification_receipts`、
`command_idempotency`、`resource_leases`、`side_effect_intents`、`artifacts`、`reviews`。

**`task_claims` 不在其中。** 它只出现在三处：

- §8 开头那份 41 张表的**名单**（只有表名，无字段）；
- `claim_requests` 行的**说明文字**里；
- §37.6 补充清单，写作 `| task_claims/task_attempts | owner、attempt_no、epoch、
  lease、base/head commit、status | 调度中心 |` —— 一行合并两张表，列出的字段描述的
  是 `task_attempts`，**没有一个字**涉及 `claim_request_id` 或 per-task 幂等。

后果：改动之后，**per-task 幂等的权威约束（哪张表、哪个复合键、哪些列、谁写）在
§8/§37.6 里没有任何正式定义**，只存在于 §11 的散文和 `claim_requests` 行的括号说明里。
而 R10-fix-09 自己的记录里写明，`schema-keys` 断言组**跳过**含 `/` 或 `；` 的 §37.6
单元格 —— `task_claims/task_attempts` 恰好含 `/`，正属于被跳过的"已知缺口"。即：

> 由 `schema-keys` 组抓出来的那类缺陷，在它的修法落点上，**恰好落回了同一个断言组的
> 盲区**。目前没有机械手段能发现 `task_claims` 的定义缺失。

**处置建议**（不在本 prep 的权限内，需 R10 复审）：

- 给 §8 最小定义表补一行 `task_claims`，声明其主键、`unique(claim_request_id,
  task_id)`、关键列与写入方。**具体列名以 `001_initial.sql` 已实现的结构为准，
  先读 schema 再写规范，不要反过来**；
- 把 §37.6 的 `task_claims/task_attempts` 合并行拆开，让 `schema-keys` 组能覆盖；
- 或在 §29.6 的 R11 复审点里加一条："§8 最小定义表必须覆盖所有承载唯一约束的表"。

在此之前，本 prep 的处理是：契约与 `idempotency.md` 一律按 §11 的散文口径写，
并在 `check_contract.py` 里加 J 组断言，防止**我方契约**再把复合键放回
`claim_requests`。但这只是防我自己，不能替代规范修复。

---

## 5. 对 MVP-02（schema）的要求

这份契约要在 schema 落地后能直接实现。清单 MVP-02 的交付列表已覆盖大部分，
这里只列**契约额外的、清单或 R10 正文没点名的**部分。**前三条来自 R10-fix-09，
优先级最高** —— 它们对应的 schema 缺陷已经真实发生过一次。

1. **`task_claims` 必须承载 per-task 幂等**：`unique(claim_request_id, task_id)`，
   且能表达"这一项当时是什么结果"（含失败项的释放原因）。回放走
   `SELECT * FROM task_claims WHERE claim_request_id = ?`，**不从
   `first_result_json` 反解**（§11，R10-fix-09）。
   字段清单已由 R10-fix-10 落进 §8（`claim_id, claim_request_id, task_id,
   attempt_id, owner, claimed_at, released_at, release_reason`），与本条要求一致；
   以 §8 行为准，`001_initial.sql` 如与 §8 有出入则改实现（Q-6 已闭环）。
2. **`claim_requests` 不得有 `task_id` 列**，也不得在该表上再建含 `task_id` 的
   复合唯一键。它是每请求一行（§8，R10-fix-09）。
3. `claim_requests` 需要 `partial_allowed` 落库（§11 R8 约束明确要求"批量
   `partial_allowed` 必须落库，默认 false"）。
4. `command_idempotency` 的查询必须能按 `(operation, idempotency_key)` 一次取回
   `payload_hash` **和** `first_result`（§8 已有字段），且 `first_result` 要能存
   完整的批量响应 JSON。若只存标量，D-3 的重放返回首次结果无法实现。
5. 事件查询需要 `seq` 上的索引，以及 `(task_id, seq)` 复合索引 ——
   `read_context(since_seq)` 和 `GET /tasks/{id}/events?since_seq=` 都是热路径。
6. `task_messages` 需要能按 `since_seq` 增量取，且与 `task_events` **共享同一个
   全局 seq 空间**（§15 全局 seq 是排序权威）。若消息表用独立自增，D-4/D-5 的
   统一游标就不成立。**这条最容易踩坑**，建议 MVP-02 明确写进 schema 说明。
7. `reviews` 需要 `review_lease_token`（或 R10 裁定的等价字段），见 Q-3。
8. `verification_receipts` 的 `receipt_hash` 要能通过 API 只读暴露给前端
   （MVP-09 要显示验证结果），但权威副本不可写（§16）。契约里所有 receipt
   端点都是 GET。

---

## 6. 本 prep 的验收方式

prep 不进主分支、不在 `./scripts.sh check` 门禁内（该门禁只跑 `tools/docstate`、
`tools/discipline`、`backend/` 下的 ruff 与 pytest）。但为了不只停在"文档声称闭环"
（清单 H-2 的理由），本 prep 自带一个可机械执行的校验脚本 `check_contract.py`，
`tools/docstate` 扩围时可直接吸收其中的 A～J 组思路。

运行（**必须用仓库 venv 的 python**，脚本用 PyYAML 解析 OpenAPI）：

~~~bash
cd docs/prep/MVP-08
../../../.venv/Scripts/python.exe check_contract.py            # 默认读 <repo>/docs/spec/0.3-R10.md
../../../.venv/Scripts/python.exe mutation_test.py
../../../.venv/Scripts/python.exe check_contract.py --r10 <path> --here <dir>
~~~

R10 路径由脚本自身位置推出（`parents[3]/docs/spec/0.3-R10.md`），所以从任意
worktree 里跑都指向本工作树的 R10，不会误读别处的副本。

它断言的**十组**内容：

| 组 | 断言 |
|---|---|
| A | 从 R10 §10 / §37.7 的**实际元素**算出状态数与 M/O/R 三集合大小，并验三集合两两不交、各自无重复 |
| B | `openapi.yaml` 是合法 YAML、OpenAPI 3.1、全部 `$ref` 可解析、server URL 只含 loopback |
| C | 契约的 `TaskStatus` 枚举集合 **等于** §10 状态集合；`child_created` 不在其中 |
| D | 契约的 `EventName` 枚举集合 **等于** §37.7 的 M∪O∪R，且长度相等、无重复 |
| E | §7 的 16 个动词**逐个**有对应 operationId；`/adapter/*` 的 path 尾段拼写与 §7 原文一致 |
| F | §21 的 6 个动词**逐个**有对应映射 |
| G | 契约中所有被 `enum` 化的 error code 都在 `error-codes.md` 里有定义 |
| H | `error-codes.md` 里每个带 `(M)`/`(O)`/`(R)` 标注的事件名，都真的在对应集合内 |
| I | 契约里出现的手写计数只作为提示，不作为断言常量（§37.7 方法论） |
| **J** | **Claim 幂等落点回归**：§8 `claim_requests` 行不得声明含 `task_id` 的复合键、不得有 `task_id` 列；`task_claims` 行必须存在且声明 `unique(claim_request_id, task_id)`、`task_id` 同时在键列与字段列、保留失败项留行规则；§11 必须保留"不从 JSON 里反解"和"失败项同样留行"；`idempotency.md` 不得把复合键放回 `claim_requests`；§8 的节边界必须止于 §9 之前 |

J 组存在的直接理由：本 prep 的第一版把 per-task 复合键放在了 `claim_requests` 上，
正是 R10-fix-09 修掉的那个缺陷。没有 J 组，A～I 组全绿也发现不了。

最后一条（节边界）是被 R10-fix-10 逼出来的：修好后 §8 与 §37.6 **各有一行
`task_claims`**，而本脚本的 `section_bounds` 当时因层级解析写错（把标题里第一个
空格分隔的 token 当层级，而 `## 8. Canonical 数据模型` 在 `##` 后紧跟空格，于是
层级为空、边界一路延伸到文档末尾），使 §37.6 的那行**覆盖**了 §8 的那行，J 组转红。
边界修正后 A～I 组也一并变严格（此前有若干组是"恰好取到第一个代码块"才通过的）。
详见 §6.3。

### 6.1 最近一次运行结果（2026-10-05 09:5x，对仓库 `docs/spec/0.3-R10.md` @ `32f4e45`）

~~~text
counted: states=23  M=74  O=13  R=1  union=88
paths: 34   schemas: 27   $refs: 28 (all resolve)
section 7 verbs: 16/16 mapped   section 21 verbs: 6/6 mapped
error-codes.md defines 86 codes; contract enumerates 6 under a `code` property, all defined
25 tagged event citations, all in the correct M/O/R set
J: 13/13 pass
FAILURES : 0   (exit 0)
~~~

**A 组顺带证伪了清单 H-1**：§10 的状态块实际是 **23** 个，不是清单写的 24 个。
这正是 §37.7 结论"allowlist 的任何数字都不得手写"要防的事，只不过这次错在清单而
不在 R10。见 Q-1。

**J 组的哨兵已履行使命并删除**：它原本断言"§8 没有 `task_claims` 行"，用来防止
Q-6 被静默修掉。R10-fix-10（`32f4e45`）修复后它按设计转红，于是改为对修复结果的
正向断言（`task_claims` 行存在、键列与字段列都声明 `task_id`、保留失败项留行规则）。
**让修复触发红灯是有意为之**：否则那份"已知缺口"的描述会变成陈旧谎言，而下一个人
读到时无从判断它是否still成立。

### 6.2 校验脚本自身被变异测试过

"0 失败"只有在脚本真的会失败时才有意义。`mutation_test.py` 把契约复制到临时目录、
每次只注入一个缺陷，断言 `check_contract.py` 退出非 0 **且**报出正确的那条断言。
10 个变异全部被抓到：

| 变异 | 应触发的断言组 |
|---|---|
| 往 `EventName` 里塞一个自造事件名 `task_resurrected` | D |
| 从 `TaskStatus` 删掉 `reclaiming` | C |
| 把 `child_created` 加进 `TaskStatus` | C |
| `/adapter/submit_result` 改成 `/adapter/submitResult` | E |
| server URL 改成 `0.0.0.0` | B |
| `$ref` 指向不存在的 `ClaimResult` | B |
| `enum` 里塞一个未定义的 `plan.circular_dependency` | G |
| 把 `idempotency_conflict` 的 `(O)` 标注改成 `(M)` | H |
| 引用一个不在 allowlist 的事件名 `attempt_budget_exceeded` | H |
| **把 per-task 复合键从 `task_claims` 挪回 `claim_requests`** | **J** |

最近一次运行（2026-10-05）：`caught 10/10`，退出码 0。

### 6.3 这套脚本**没有**覆盖的

诚实列出，避免把"0 失败"读成"契约正确"：

- **H 组只校验带 `(M)`/`(O)`/`(R)` 标注的引用**。`error-codes.md` 正文里还有若干
  未加标注的事件名提及（例如 §2.4 说明段里的 `lease_expire`），不在校验范围内；
- **G 组只校验被 `enum` 化的 code**。描述文字里提到的错误码（如 README 里的示例）
  不校验 —— 描述不是规范；
- **J 组只查 R10 有没有那几句话，不查语义**。R10 若把"不从 JSON 里反解"改成反义句，
  字符串匹配照样通过。要真正锁住语义，得解析 §11 的结构，那是 R10 复审的事；
- **变异测试无法覆盖 R10 侧**。`mutation_test.py` 只把契约文件复制到临时目录，
  R10 始终读真实仓库副本 —— 因为"变异真相源"本身就不是这套工具该做的事；
- **节边界回归靠一条哨兵断言 + 一次检查器自变异**。`section_bounds` 曾因层级解析写错
  （把标题里第一个空格分隔 token 当层级，而 `## 8. ...` 在 `##` 后紧跟空格 → 层级为空
  → 边界延伸到文档末尾），使 §8/§10/§11/§37.7 的边界**全部过宽**。当时 A～I 组之所以
  全绿，是因为它们恰好只消费"区域内第一个代码块"或用全文档子串匹配 —— **是碰巧，
  不是正确**。暴露的契机是 R10-fix-10 让 §37.6 也出现了一行 `task_claims`，覆盖了
  §8 的那行。

  修好后加了显式断言（"§8 的边界必须止于 §9 之前"）。**该断言已证伪**：把
  `section_bounds` 的两行改回旧写法后重跑，它连同 3 条下游断言一起转红
  （`FAILURES: 4`）：

  ~~~text
    FAIL  section 8 bounds stop before section 9 (section_bounds regression guard)
    FAIL  task_claims row declares unique(claim_request_id, task_id)
    FAIL  task_claims row declares task_id in both the key and field cells
    FAIL  task_claims row keeps the failed-item-still-writes-a-row rule
  ~~~

  复现方法：把 `level = len(heading) - len(heading.lstrip("#"))` 换成
  `level = heading.lstrip("#").split(" ")[0]`、把 `^#{{1,{level}}}\s` 换成
  `^#{{{1},{len(level)+1}}}\s`，用 `--r10 <真实 R10> --here <临时目录>` 跑。
  这一步没有并进 `mutation_test.py` —— 那个脚本只变异契约文件，不变异检查器自身；
  要自动化它得再加一层，本轮未做。
- **没有校验 R10 §10 迁移表本身**。即契约里的 `(status, event)` 组合是否真的都是
  §10 的一条边，本脚本不查。这需要先把 §10 迁移表解析成结构化数据，属于
  `tools/docstate` 的 `DOC-STATE-001` 职责，不该在 prep 里重复实现一份
  （否则又造出第二份真相源）；
- **没有校验 HTTP 语义是否合理**。503 该不该用在 `submit.verify_capacity_full`
  上是设计判断，脚本查不了；
- **`openapi.yaml` 未做完整 OpenAPI 3.1 规范校验**（没用 spectral / openapi-core），
  只做了 YAML 可解析 + `$ref` 可解析 + 版本字段。MVP-08 实现前应补一次
  正规校验器；
- **本脚本不是零外部依赖**。`tools/docstate` 与 `tools/discipline` 都刻意只用标准库，
  本脚本用 PyYAML（OpenAPI 解析所必需）。PyYAML 目前是 `uvicorn[standard]` 的传递
  依赖，已在 venv 里；但如果哪天 venv 重建且去掉该 extras，本脚本会以退出码 2 明确
  报错而不是静默跳过。**若 MVP-08 要把这些断言并入 `tools/docstate`，必须先把
  YAML 解析换成标准库实现或正式把 PyYAML 写进 requirements。**

