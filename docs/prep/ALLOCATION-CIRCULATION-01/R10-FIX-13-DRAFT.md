# R10-fix-13 草案：方案传阅与分配仲裁（规范修订输入）

状态：**Stage A 已完成并验证**；**Stage B 已完成并验证**（传阅 + 分配 + 路由 + 权限）；
**Stage C 未开始**（管理员 harness 编排 + 前端两个面）。

唯一**故意留着没做**的一件事：把 `claim:authorized` 变成 `/adapter/claim` 的**强制门禁**。
理由与可选方案见 §10 —— 它会让现存的每一条老流程与全部旧测试都失败，属于需要群主点头的
架构选择，不能由实现方顺手决定。

---

## 0. 落地现状（截至本次修订）

**Stage A（规范登记）**：`docs/spec/0.3-R10.md` 新增 A 集合（18 项）与 **§39 分配与传阅**、
§8 追加 8 个实体、§17 追加权限行（含 `circulation:cancel`）、§29.3/§29.6 计数同步、
§38.11 修复记录；`tools/docstate/docstate.py`（98 项检查 0 失败，新增 `[allocation]` 组：
§39↔A 双向封闭）；`backend/app/spec.py`；`backend/app/events/store.py`（allowlist 解析块数
3 → 4）；`backend/tests/test_spec_mirror.py`。

**Stage B（迁移 + 服务 + 状态机 + 路由 + 权限）**：

| 文件 | 内容 |
|---|---|
| `backend/migrations/016_allocation.sql` | 8 张表；两条不变量交给索引（见 §4） |
| `backend/app/allocation/machine.py` | §39 两台状态机的代码镜像（**22 条边**） |
| `backend/app/allocation/service.py` | 传阅 6 个动作 + 分配 11 个动作 + Claim 授权查询 |
| `backend/app/api/routes.py` / `schemas.py` | 20 条路由（`/api/v1/allocation/...`） |
| `backend/app/auth/policy.py` | 15 个新键：POLICY 逐行镜像 + `ADMIN_HARNESS_KEYS` 委托 |
| `backend/tests/test_allocation_machine.py` | 逐边与正文比对（不是回读注释） |
| `backend/tests/test_allocation_circulation.py` | 12 项（含硬门禁与轮次回归） |
| `backend/tests/test_allocation_service.py` | 24 项（申请/裁决/接单/撤销/回避/封闭规则） |
| `backend/tests/test_allocation_routes.py` | 8 项（HTTP 权限面、身份绑定、硬门禁、乐观锁） |

### 写服务时抓到、并已修掉的真 bug

这三个都**不是**笔误，而是"照正文抄一遍才会暴露"的结构问题：

1. **第二轮传阅谁都提交不了**（`circulation_reviews` 唯一键少了 `round_no`）。
   §39 的 `revising → circulating` **复用同一条**传阅记录，于是第一轮评过的人在第二轮被唯一键
   挡住 → 传阅永远到不了 `completed` → 硬门禁反过来把整个流程锁死。修法：唯一键带 `round_no`，
   `revise` 让轮号前进（并可换本轮名单）。回归：`test_a_round_one_reviewer_may_review_round_two`。
2. **一张分配轮只能分配一个任务**。分配轮有一次裁决就进入 `decided`，而
   `allocation.decision_made` 只登记了 `closed → decided` 一条边 → 同轮第二个任务被状态机拒绝。
   §10 处理"同一事件重复发生"用的是自环（已有 7 条），§39 缺了同款。修法：正文与镜像各补
   `decided | allocation.decision_made | 同一轮内继续为另一个任务裁决 | decided`
   （以及 `allocation.decision_deferred` / `allocation.material_requested` 在 `decided` 上的自环，
   理由相同：进入裁决阶段后，同轮其余任务不能变成死路）。
3. **没人申请的任务发不出邀请**。`assignment.invited` 只从 `decided` 出发，而"冻结之后才发现
   没人申请"时轮次还在 `closed` → 邀请被拒。修法：正文写成 `closed/decided | assignment.invited | ... | decided`。

另有一处**实现与已登记正文对不上**：§38.11 把申请状态写成 `submitted/frozen/withdrawn`，
而第一版服务只有 `submitted/withdrawn`。修法是让实现跟上正文：`close_allocation_round` 把仍在
候选集合里的申请转 `frozen`（还能被裁决，但本人已改不了、也撤不回 —— §39 的 `application.*`
三条边都只从 `opened` 出发）。

### 顺带修掉的一处门禁红灯

Stage A 让 docstate 的断言数 90 → 98，而 `README.md` / `docs/dev-convention.md` 里仍写着 90。
`tools/discipline` 的 `[counts]` 组**专门**防这件事（"加了断言忘了改文档，文档就开始骗人"），
于是 `discipline` 转红并连带 3 个 `test_gate_utf8` 失败。已把两处声明改成 98。

来源与依据：

- 群主 2026-10-17 决定：`D:/w/office-preview/handoff/TASK-ALLOCATION-ARBITRATION.md` §7 的
  分配/传阅后端**真做**，不走"用现有机制近似"的路线。
- 该文件 §117 明确：新事件名、状态和 schema **经规范复审登记后再落代码**；§128 明确
  当前权限矩阵只有 `human`/`harness` 主体，管理员身份需要显式委托。
- 产品流程的规范出处：`handoff/START-HERE.md` §2（用户发布需求 → 管理员拆解 → 成员传阅
  评论 → 管理员整理 → 再认领/协助/完成/独立审查；默认两轮传阅）、
  `handoff/dispatch-2026-10-11/PLAN-CONTRACT.md`、`src/plan-runtime.js`（mock 状态机）。

写在最前面的一条：**本子系统的事件不是"观测"**。`allocation.decision_made` 会授权
一个 attempt 开工，把它塞进 O 集合（定义是"由巡检器、安全扫描或幂等层写出，
不驱动任务状态迁移"）会伪造语义、并让"授权"失去可审计的业务事件身份。因此本草案
新增**第四个 allowlist 类别 A（分配事件）**，而不是复用 O。

---

## 1. 冲突

| 出处 | 规定 |
|---|---|
| `TASK-ALLOCATION-ARBITRATION.md` §7 | 需要 `allocation-rounds` / `applications` / `decisions` / `assignments` 等实体，以及"事件写入与投影修改原子提交、重启可回放" |
| §37.7 封闭规则 3 | **§10 迁移表的每一条边的 `event_name` 必须在 M 集合内** |
| §37.7 封闭规则 5 | 实现层对不在 allowlist 中的事件必须拒绝并告警；新增事件必须提升 `schema_version` |
| §37.7 导语 | allowlist 只有 M/O/R 三类；`app/events/store.py` 的正则要求**恰好三块**，否则 `ValueError` |
| §10 | 任务状态机里没有"传阅中/竞争中"，`draft` 是**任务**自己的状态；分配状态与任务执行状态必须分开（§117） |
| §17 | 权限矩阵没有分配/传阅相关键 |

## 2. 裁定一：新增 allowlist 类别 A（分配事件，18 项）

> 4. 分配事件（A）：直接对应 §39 分配与传阅表的一条边，必须唯一决定 `next`。
>    与 M 同构（驱动状态迁移），但迁移的是**分配状态**而非 `tasks.status`；
>    封闭规则 6 的约束对 A 同样成立——A 事件不得改写 `tasks.status`。

A 集合（18 项）：

~~~text
circulation.opened  circulation.review_submitted  circulation.round_closed
circulation.revised  circulation.completed  circulation.cancelled
allocation.opened  allocation.closed  allocation.material_requested
allocation.decision_made  allocation.decision_deferred
application.submitted  application.updated  application.withdrawn
assignment.invited  assignment.accepted  assignment.declined  assignment.revoked
~~~

计数：M=74 / O=14 / R=1 / **A=18**，并集 **107**。

## 3. 裁定二：新增 §39 分配与传阅（两台状态机，22 条边）

不复用 `tasks.status`，也不往 `TaskStatus` 里加"竞争中"。

### 3.1 传阅（circulation，方案层）—— 6 条边

状态：`circulating → revising → (circulating | completed) → cancelled`

~~~text
absent | circulation.opened | 管理员发布方案草案、本轮传阅名单与轮数上限 | circulating
circulating | circulation.review_submitted | 名单内某成员提交本轮意见与材料版本 | circulating
circulating | circulation.round_closed | 本轮名单全部提交或本轮截止时间到 | revising
revising | circulation.revised | 管理员按本轮意见修订方案并产生新方案版本 | circulating
revising | circulation.completed | 已完成轮数达到配置轮数且方案定稿冻结快照 | completed
circulating/revising | circulation.cancelled | 群主取消或方案作废 | cancelled
~~~

- **没有 `draft` 状态**：传阅轮只能被"开启"（`absent --circulation.opened--> circulating`）。
  管理员在开轮之前自己起草方案是它的模型工作，不是平台状态；留一个没有入边的 `draft` 只会
  让人以为存在一个可观测的起草阶段。`absent` 因此是**创建边专用伪状态**（同 §10 的 `child_created`）。
- **硬门禁（群主 2026-10-17 决定）**：`allocation.opened` 的 guard 要求绑定的方案快照已完成
  **至少 1 轮**传阅。未传阅就分配 = 拒绝（409 `allocation.refused`），不是前端提示。
  轮数配置区间因此为 **1–3，默认 2**；`>3` 只留确认提醒。"0 轮也可分配"不存在。

### 3.2 分配（allocation，任务层）—— 16 条边

状态：`opened → closed → decided → cancelled`；申请独立为 `submitted → frozen | withdrawn`；
一份分配为 `invited → accepted | declined | revoked`。

~~~text
absent | allocation.opened | 管理员开分配轮：方案快照已批准、已完成至少 1 轮传阅、绑定截止时间 | opened
opened | application.submitted | 主体为本人、任务属于本群、轮次未冻结 | opened
opened | application.updated | 本人、未冻结、expected_revision 匹配 | opened
opened | application.withdrawn | 本人撤回 | opened
opened | allocation.closed | 截止时间到或管理员关闭，候选集合冻结 | closed
closed | allocation.material_requested | 管理员要求某申请补材料并重开该任务的材料审核 | closed
decided | allocation.material_requested | 裁决阶段中为同轮另一个任务要求补材料并重开其材料审核 | decided
closed | allocation.decision_made | 管理员选定候选，回避规则通过、expected_revision 匹配 | decided
decided | allocation.decision_made | 同一轮内继续为另一个任务裁决（一轮多任务，每任务一条裁决） | decided
closed | allocation.decision_deferred | 管理员暂不分配并记录理由 | closed
decided | allocation.decision_deferred | 已进入裁决阶段后再暂不分配另一个任务并记录理由 | decided
closed/decided | assignment.invited | 管理员给未申请者发改派邀请（含无人申请的任务） | decided
decided | assignment.accepted | 被选中者或受邀者本人确认接单 | decided
decided | assignment.declined | 受邀者本人拒绝改派邀请 | decided
decided | assignment.revoked | 管理员撤销尚未 Claim 的分配，需理由与版本 | decided
opened/closed/decided | circulation.cancelled | 所属方案作废，未执行分配一并失效 | cancelled
~~~

几条**刻意**的设计与它们的可执行形式：

- **申请不等于领取**：`application.submitted` 不产生 owner/attempt/Lease/工作区，也**不写**
  `tasks.status`（`test_the_whole_allocation_flow_never_touches_the_task` 钉住）。
- **裁决不等于授权**：裁决只生成一份 `invited` 的分配；被选中者还要自己 `accept` 才构成
  Claim 授权。管理员的"选"和本人的"接"是两条事件。
- **`allocations.state` 与 `allocation_rounds.state` 是两套**：前者回答"这份授权能不能用来
  Claim"（`invited/accepted/declined/revoked`），后者是 §39 那张表的轮次状态。
- **`allocations.claimed_at` / `claim_id`**：§39 只允许撤销"尚未 Claim 的分配"。`accepted`
  本身只说明被选中者答应了，不说明他已经拿着它开了工；没有这两列就分不出"撤销一份没人用的
  授权"和"撤掉一个正在跑的 Lease"，而后者会留下孤儿工作区。
- **暂缓不写 `allocation_decisions`**：那条表的唯一键是 `(round_id, task_id)`，语义是
  "一轮一任务一条裁决"。暂缓不是裁决——写进去会把该任务在这个轮次里永久钉死，之后想改主意
  就再也分配不了。暂缓的持久记录是事件本身（§115 可回放）。
- **回避（§39 硬规则 3）**：裁决者不得是该任务的有效候选。冲突时**拒绝**，按正文交给群主，
  不是"换个人选"。
- **乐观锁（§39 硬规则 4）分三层**：申请动作对 `applications.revision`，轮次动作对
  `allocation_rounds.revision`，分配动作对 `allocations.revision`。轮次版本在**每一条边**上
  都 +1，所以"看着旧候选名单拍板"必然撞车。

## 4. 裁定三：§8 canonical 新增实体（8 个）

| 实体 | 关键列 |
|---|---|
| `circulation_rounds` | circulation_id、group_id、plan_id、plan_snapshot_id、round_no、reviewer_harness_ids、opened_by、opened_at、deadline_at、closed_at、state、revision |
| `circulation_reviews` | review_id、circulation_id、**round_no**、harness_id、instance_id、body、material_hash、submitted_at |
| `allocation_rounds` | round_id、group_id、plan_snapshot_id、plan_version、circulation_id、opened_by、opened_at、deadline_at、closed_at、state、revision |
| `applications` | application_id、round_id、group_id、harness_id、instance_id、state、reason、revision、submitted_at、updated_at、withdrawn_at |
| `application_tasks` | application_id、task_id（`unique(application_id, task_id)`） |
| `application_materials` | material_id、application_id、version、content_hash、body、created_at（不可变，版本只增不改） |
| `allocation_decisions` | decision_id、round_id、task_id、chosen_application_id、compared_json、decider_harness_id、outcome、reason、revision、decided_at |
| `allocations` | allocation_id、round_id、task_id、harness_id、decision_id、state、accepted_at、declined_at、revoked_at、**claimed_at**、**claim_id**、revision |

两条不变量由**索引**而不是靠应用逻辑钉住：

1. `allocation_decisions` 的 `unique(round_id, task_id)`：同一轮同一任务最多一条裁决，
   两个管理员同时裁决只有一个成功。
2. `allocations` 的部分唯一索引 `ON task_id WHERE state='accepted'`：一个任务在任一时刻
   最多一份"已确认、可用于 Claim"的授权；撤销与 Claim 并发时只有一方成功。

## 5. 裁定四：§17 新增权限键

| 操作 | 人类管理员/负责人 | Planner | 开发 Harness | 协作者 | Reviewer | Verify Worker | Merge Worker |
|---|---|---|---|---|---|---|---|
| `circulation:start` / `:close` / `:revise` / `:complete` / `:cancel` | 允许 | 拒绝 | 拒绝（**管理员 harness** 经 `AP_ADMIN_SUBJECTS` 委托允许） | 拒绝 | 拒绝 | 拒绝 | 拒绝 |
| `circulation:review` | 拒绝 | 拒绝 | 允许（被列入本轮名单者，仅本轮） | 拒绝 | 拒绝 | 拒绝 | 拒绝 |
| `allocation:open` / `allocation:close` | 允许 | 拒绝 | 拒绝（**管理员 harness** 委托允许） | 拒绝 | 拒绝 | 拒绝 | 拒绝 |
| `allocation:submit` | 拒绝 | 拒绝 | 允许（仅本人，拒绝冒用他人 ID） | 拒绝 | 拒绝 | 拒绝 | 拒绝 |
| `allocation:material` | 允许 | 拒绝 | 允许（仅本人申请） | 拒绝 | 拒绝 | 拒绝 | 拒绝 |
| `allocation:decide` | 允许 | 拒绝 | 拒绝（**管理员 harness** 委托允许；且须回避） | 拒绝 | 拒绝 | 拒绝 | 拒绝 |
| `assignment:accept` / `assignment:decline` | 拒绝 | 拒绝 | 允许（仅被选中/受邀者本人） | 拒绝 | 拒绝 | 拒绝 | 拒绝 |
| `assignment:revoke` | 允许 | 拒绝 | 拒绝（**管理员 harness** 委托允许） | 拒绝 | 拒绝 | 拒绝 | 拒绝 |
| `claim:authorized` | 允许 | 拒绝 | 允许（**仅当存在本人、该任务、已 accepted 且未失效的分配**） | 拒绝 | 拒绝 | 拒绝 | 拒绝 |

**键的合并是刻意的**（避免矩阵多出同权限的行，也避免前端为一个动作换两次身份）：

- `allocation:submit` 覆盖同一条边的三次本人动作：**提交 / 修改 / 撤回**自己的申请
  （"一个人一轮一份"，改口是改同一份）。
- `allocation:decide` 同时覆盖**裁决**、**暂缓**与**改派邀请**：三者都是"管理员对冻结后的
  候选集合做处置"。
- 管理员 harness **不靠"我是管理员"字段**，也不借用群主 bearer token：沿用 2026-10-17 已
  落地的 `AP_ADMIN_SUBJECTS` 主体白名单（加性、fail-closed、默认空）。
- `plan.approve`（群主批准）**保持不变**，仍是人类键：分配不能代替批准。

## 6. 裁定五：API 契约（已实现）

实际落地在 `/api/v1/allocation/...`（不是早期草案里的 `/adapter/...` 动词式路径：分配面
同时有"管理员面"和"成员本人面"两组动作，用一套子前缀比把它们混进 `/adapter` 更好读）：

| 方法 | 路径 | 权限键 |
|---|---|---|
| POST | `/circulation/open` | `circulation:start` |
| POST | `/circulation/review` | `circulation:review` |
| POST | `/circulation/close` | `circulation:close` |
| POST | `/circulation/revise` | `circulation:revise` |
| POST | `/circulation/complete` | `circulation:complete` |
| POST | `/circulation/cancel` | `circulation:cancel` |
| POST | `/round/open` | `allocation:open` |
| POST | `/round/close` | `allocation:close` |
| POST | `/application` | `allocation:submit` |
| POST | `/application/update` | `allocation:submit` |
| POST | `/application/withdraw` | `allocation:submit` |
| POST | `/material/request` | `allocation:material` |
| POST | `/decision` | `allocation:decide` |
| POST | `/decision/defer` | `allocation:decide` |
| POST | `/invite` | `allocation:decide` |
| POST | `/accept` | `assignment:accept` |
| POST | `/decline` | `assignment:decline` |
| POST | `/revoke` | `assignment:revoke` |
| GET | `/authorization?task_id=&harness_id=&expected_revision=` | `claim:authorized` |
| GET | `/candidates?round_id=&task_id=` | 读接口（群主可查看材料，§39 硬规则 7） |

身份一律**同时**来自请求体与 token，并经 `bind_declared` 绑死：加性部署（`AP_AUTH_MODE=off`）
下请求体是唯一身份来源；`enforce` 下"请求体里写别人的 harness_id"是 403。

`/authorization` 在"没有授权"时返回 `{"authorized": false}` 而不是 403 —— 那是**答案**，
不是错误；只有 `expected_revision` 与手上那份分配对不上时才拒（那是"拿旧版本开工"）。

## 7. 验收

`ALLOC-001`～`014` 全文见 `TASK-ALLOCATION-ARBITRATION.md` §8；本草案补充 `CIRC-001`～`006`。

- `CIRC-001`：方案快照完成 **0 轮**传阅时必须**拒绝**开分配轮；≥1 轮后才允许。
  `test_allocation_cannot_open_before_any_circulation` / `test_an_unfinished_circulation_does_not_satisfy_the_gate`
  / `test_the_gate_is_per_snapshot` / `test_allocation_is_refused_before_any_completed_circulation`。
- `CIRC-002`：`revising` 的两个出边各只有一条 `next`（封闭规则 2）。
- `CIRC-003`：传阅意见与申请材料都是**不可变版本**，重开只增版本
  （`test_update_replaces_the_task_set_and_advances_the_revision`）。
- `CIRC-004`：非名单成员提交意见被拒。
- `CIRC-005`：轮数配置区间 1–3（默认 2）；`>3` 只提醒，不改状态、不阻断。**待做**（Stage C）。
- `CIRC-006`：群主取消方案 → `circulation.cancelled`，在途轮次进 `cancelled`
  （`test_cancel_needs_a_reason_and_lands_in_cancelled`）。
- `ALLOC-004`：两个管理员并发裁决只有一个成功（唯一键 + `expected_revision`）。
- `ALLOC-014`：撤销与 Claim 并发只有一个成功（部分唯一索引 + `claimed_at`）。

## 8. 群主已决定（2026-10-17）

1. **事件类别**：新增 **A 类别**（分配事件，18 项），不复用 O。Stage A 必须在**同一次
   提交内**同步四个解析器与全部计数点，否则门禁转红。
2. **传阅是硬门禁**：`allocation.opened` 要求方案快照已完成 ≥1 轮传阅，否则拒绝。
   派生的 `CIRC-001` 因此是硬断言。
3. **管理员身份**：`AP_ADMIN_SUBJECTS` 主体白名单（加性、fail-closed、默认空），不新增
   主体类型，不给普通成员任何新增权限。

（早先版本在本节还留了两条"待群主选"的开放项 —— A 类别 vs 复用 O、传阅是否强制 ——
上面第 1、2 条就是群主的答复，开放项已关闭。）

## 9. Stage C：还差什么

1. `/adapter/claim` 的门禁强度（见 §10）—— 需要群主定。
2. 管理员 harness 的编排：`refine → 开传阅 → 收意见 → 修订 → 定稿 → 开分配轮 → 裁决 →
   通知接单`，把它做成管理员面的一次可点击流程。
3. 前端两个面：群主面只留"提需求 / 回答管理员提问 / 全局观察"，管理员面承载传阅 + 分配 +
   裁决 + `plan.approve`。
4. 迁移 016 应用到线上库（目前只在临时库验证过）。
5. `circulation:start` 的**轮数配置**（1–3，默认 2 + `>3` 提醒）落库与落 UI。

## 10. `claim:authorized` 强制到哪一档（群主 2026-10-17 已裁定）

§39 硬规则 2 的字面写法是"只有已 `accepted` 的分配才构成 §11 Claim 的授权"。**严格实现**
意味着：现在库里每一条没有分配的任务（57 条）都再也无法被 Claim，`test_real_adapters`、
`test_claim_*`、三条已跑通的合并链全部失败。这不是小改，是"换掉领取机制"。

三条可选路线与裁定：

- **(a) 严格**：Claim 必须带 `allocation_id` + `expected_revision`。最贴合正文，但要求为
  所有存量任务补分配，且老 adapter 全要改。**未采用。**
- **(b) 分群开关**（推荐，**群主已选**）：默认不强制（存量流程照旧），对
  `AP_ALLOCATION_REQUIRED_GROUPS` 里列出的群强制。门禁真实存在、可被验证，
  又不让整库立刻不可用。
- **(c) 只读授权**：`/authorization` 可查、Claim 不拦。**最弱**，等于把 §39 硬规则 2 降级成
  建议 —— 与群主"要真做、不要近似"的要求相冲。**未采用。**

### 落到代码的样子（Stage B 末补，已实现并有 14 项测试）

门禁在 `app/claim/service.py::_grant_claim` 里，紧接着既有的 `eligibility` 检查：

- **按群**：`settings.allocation_required_groups`（`AP_ALLOCATION_REQUIRED_GROUPS`，
  逗号分隔，默认空）。空集合 = 一个群都不强制，存量行为逐字不变（有测试钉住）。
- **读口径只有一处**：`app/allocation/service.py::authorization_row`。`ClaimService`
  必须**在它已经持有的那个事务上**问这个问题 —— `Database.transaction()` 每次开新连接，
  嵌套进去会在 `BEGIN IMMEDIATE` 上互相等到超时，所以没有复用
  `AllocationService.claim_authorization`（那个自带事务）。
- **要求**：`state = 'accepted'`、`claimed_at IS NULL`（没用过）、
  `harness_id` 与 `task_id` 都对得上、且**分配轮绑定的快照 = 任务当前快照**
  （硬规则 2 的"计划版本"一格，顺带让硬规则 5"换版后旧授权失效"真的可执行）。
- **拒绝方式**：`claim.not_authorized`（409 语义，已登记进
  `docs/prep/MVP-08/error-codes.md` §2.3）。拒绝不写 attempt / Lease / 工作区，
  也不改 `tasks.status`。
- **只拦第一份 attempt**：`changes_requested` 的返工继续的是同一份已被授权的作业。
  若返工也要新分配，一次 verify 失败就会把任务钉死等分配。
- **用掉才记账**：真正开出 attempt 时 `consume_authorization` 把 `claimed_at`/`claim_id`
  写在**同一行**上，`claim_id` 与 `task_claims.claim_id` 同值 —— 这样"撤销一份没人用的
  授权"与"撤掉一个正在跑的 Lease"可以 join 分开判断。排队等资源（`queued`）不消耗授权。
- **尚未强制**：`expected_revision`（分配版本）。§11 的 Claim 请求里没有这个字段，强制它
  要给 `/adapter/claim` 加字段并让所有调用方带上。登记为待办，不假装已覆盖。

---

## 11. 编辑清单

**Stage A（规范登记，必须原子完成）** —— 已完成：

| 文件 | 改动 |
|---|---|
| `docs/spec/0.3-R10.md` §37.7 | 导语三类 → 四类；新增「分配事件（A，18 项）」块；计数 89 → 107；封闭规则 1 扩为四集合；封闭规则 3 增 §39→A 映射；`DOC-STATE-001` 断言增 A=18 |
| `docs/spec/0.3-R10.md` §39（新增章） | §3 的 22 条边与硬规则 |
| `docs/spec/0.3-R10.md` §8 | 追加 §4 的 8 个实体 |
| `docs/spec/0.3-R10.md` §17 | 追加 §5 的权限行 |
| `docs/spec/0.3-R10.md` §29.3 / §38.11 | 计数同步 / 修复记录 |
| `tools/docstate/docstate.py` | 「分配事件（A，18 项）：」标题、计数、`[allocation]` 组 |
| `backend/app/spec.py`、`backend/app/events/store.py` | 同一次提交内同步 |
| `README.md`、`docs/dev-convention.md` | 断言数 90 → 98（`discipline [counts]` 会红） |

**Stage B** —— 已完成（见 §0 表）。**Stage C** —— 见 §9。
