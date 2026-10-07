# 权限矩阵设计输入（吸收队列 ④；供未来认证任务接手）

日期：2026-10-08 ｜ 作者：Qoder（permission-matrix 卡）｜ 性质：**设计输入，非决策记录**

本报告回答一个问题：**本仓做认证层时，权限面参考什么？** 结论先行：

1. **验收尺 = R10 §17 自己的权限矩阵**（7 角色 × 11 操作，文档检查 ID
   `DOC-AUTH-001`）。它是唯一真相源；认证任务的验收 = 逐格断言，不是另造表。
2. Paperclip 的 21 个 `PERMISSION_KEYS` 与 `principal_permission_grants` 模型
   提供**机制与词汇**（键命名法、授权三元组、单点决策）——按 ADR-DEV-003 的
   边界借鉴，**不照搬键表**（它服务 company/board 域，我们没有这些概念）。
3. 三个必须先答的缺口（现成证据）：**403 审计事件名在 R10 的 88 项事件白名单
   里不存在**（需 R10-fix）；**OS ACL 层**与 ADR-DEV-002 单账户降级的关系；
   **help 协作者**的面口径。

---

## 1. R10 权威矩阵（照录）与我方落点

角色（R10 §17 L1020-1031）：人类管理员 / 项目负责人 / Planner / 开发 Harness /
协作者 / Reviewer / Merge Worker（表中"人类管理员/负责人"合一列）。

| 操作（R10） | 我方现有面 | 现状口径（2026-10-08） |
|---|---|---|
| approve_plan | `POST /api/v1/plans/{id}/approve`（+`/reject`） | 无主体校验；`decided_by` 字段由调用方自报（§18 台账留痕） |
| claim/heartbeat | `POST /adapter/claim`、`/adapter/heartbeat` | `harness_id` 实名随请求；无 token——loopback + Host 校验是唯一边界 |
| submit_result | `POST /adapter/submit_result` | 同上；fencing epoch/attempt 已由服务层校验（"自己的 attempt"有事实校验） |
| submit_plan | `POST /api/v1/groups/{gid}/plans` | 无主体校验 |
| submit_review | `POST /review/submit`（+`/review/claim`） | reviewer_harness_id 自报；receipt/lease 事实校验已有 |
| publish_task_branch | `POST /adapter/publish_task_branch` | 分支白名单（§14 命名口径）已实现；"仅 help-<n>"有命名基础（`app/git/branch.py`），**无 token 层** |
| merge/integration write | 无 HTTP 面（merge worker 进程内 + 人类走 mergeguard CLI） | 进程/工具边界，非 API 边界 |
| release_escalated | `POST /human/release`（六操作之一） | decided_by 自报 |
| pin/unpin | **无面**（R10 §18 的 pinned 决策目前只有 task_decisions 台账） | 如实标注：矩阵有行、实现无面（规划差异，认证任务一并处理或声明 N/A） |
| delete_worktree | **无面**（WorktreeManager 内部生命周期） | 同上 |
| read_secret | **无面**（v1=secret 扫描 + `secret_refs` 校验，§17 降级） | §17 完整方案（短时凭据/审计/最小权限）未实现 |

注：`release`/`abandon`（adapter）、`human` 六操作（ask_human/resume/reopen/
release/adjudicate/cancel）、`review.claim/submit`、`read` 全族（projects/
groups/tasks/events/diff/messages）都在既有测试矩阵里各有正/反路径断言；认证层
要在**不破坏这些事实校验**的前提下加主体层（先事实、后主体，或两者同事务）。

## 2. Paperclip 机制核读（21 键 + 授权模型）

21 键原文（`packages/shared/src/constants.ts:1003-1026`，照录）：

~~~text
agents:create
agents:configure
agents:suggest-changes
skills:create
skills:suggest-changes
environments:manage
tools:admin
tools:manage_connections
tools:manage_profiles
tools:view_audit
audit:view_agent_actions
tools:use
tools:manage_runtime
inbox:manage
users:invite
users:manage_permissions
tasks:assign
tasks:assign_scope
tasks:manage_active_checkouts
pipelines:write
joins:approve
~~~

**授权模型**：`principal_permission_grants = (company, principal_type{user|agent},
principal_id, permission_key, scope jsonb)` 唯一索引防重；决策集中在一个入口
（`access.ts hasPermission → authorization.decide/decidePrincipalGrant`，
`server/src/services/authorization.ts:848`）。角色是独立概念（公司角色
owner/admin/operator/viewer/member + instance_admin）。

**为什么是参考而不是照搬**：这 21 键描述的是"公司里人 vs agent 对平台资源
（skills/tools/environments/inbox/pipelines）的授权"，与 R10 的角色×操作矩阵
（针对任务生命周期的动作）是**正交的两层**。照搬键表会凭空造出我们没有的资源
（skills/tools/inbox/pipelines）。可借鉴的是**形态**，不是内容。

## 3. 可借鉴与不照搬（各三条，给认证任务）

**可借鉴**：

1. **键命名法 `resource:action`**——细粒度、可枚举、拒绝可归因（403 能带出
   `tasks:assign` 这种键）。把 R10 的 11 操作也补上资源前缀（如
   `plan:approve`、`task:claim`），拒绝审计事件就有稳定的判据串。
2. **授权三元组 (principal × key × scope)**——scope 用 jsonb 承载。我们的
   scope 维度现成：group_id / task_id / attempt 归属（"自己的 attempt"、
   "help-<n> 分支"都是 scope 事实）。ADR-DEV-002 升回多账户时，grant 表可
   直接照这个形状落 SQLite。
3. **单点决策函数**——所有入口调同一个 `decide(actor, action, resource)`，
   审计集中、拒绝路径一致。我们的落点：HTTP 层在 dependency/中间件统一，
   进程内（merge worker/verify worker）在服务入口各留一处；`guarded_write`
   保持为"事实校验"层，不与主体层混淆。

**不照搬**：

1. company/board/instance_admin 等组织概念——R10 §0 是单协调者 + 多 Harness。
2. 21 键清单本身（资源表不同：tasks/groups/plans/secrets/worktrees/verification）。
3. approvals 五态审批流——R10 的人类决策面是 §18 task_decisions + human/*，
   形态已定，不引第二套审批模型。

## 4. 缺口清单（认证任务开工前必须答的）

1. **403/ACL 审计事件名不存在**（硬缺口，已验证）：R10 §17 L1057 要求
   "所有 403 或 ACL 拒绝都写审计事件"，但 R10 的 M/O/R 三块事件白名单
   （74+13+1=88 项，`backend/app/events/store.py` 从正文解析）里**没有**
   权限类事件——用同款解析扫过，唯一近亲是 `secret_scan_blocked`。
   → 认证任务须走 R10-fix 流程加一个审计事件名（如 `permission_denied`，
   含 actor/key/resource/决定），并同提交改 schema_version。
2. **OS ACL 层与单账户降级的收口**：R10 L1055-1058 要求 Harness 账户对
   state.db/canonical/凭证目录 OS 级拒绝写，且"阶段 1 必须测试"；ADR-DEV-002
   把它降级为单账户（`AP_INSECURE_SINGLE_ACCOUNT` 默认 True）。
   → 认证任务要给出：何时、以什么验收恢复到三账户（或明确继续降级的理由与
   替代控制——Host/loopback 校验已存在）。
3. **help 协作者面**：R10 矩阵允许协作者 claim（help lease）与 publish
   `help-<n>` 分支；我们 accept_help/reject_help 已落地，但**help 工作树/
   分支的产出面**没有端到端验证（§14 提到 help-<n> 命名，`branch.py` 已有
   口径）。→ 认证任务把"协作者只能碰 help 面"变成可断言的矩阵格。
4. **read_secret 无面**：§17 最小授权（按 secret_refs / verify profile /
   merge policy 的 scope）目前完全是扫描器形态。→ 要么实现，要么在认证卡里
   显式声明 N/A 并挂到未来 secret 卡。
5. **进程面主体口径**：scheduler 巡检家族、alerts/liveness/budget/exporter
   CLI、mergeguard——它们是哪个主体、要不要走 token？（建议：进程面=调度中心
   自身主体，不经 Harness token 通道；人类工具=人类主体。结论留给认证任务。）

## 5. 建议的认证任务切片（供未来开卡）

- **A 主体与令牌**：主体模型（harness/人类/调度中心进程）+ token 签发/校验 +
  403 语义 + 审计事件（含 R10-fix）。
- **B API 层矩阵执行**：逐 verb 对照 R10 §17 矩阵的 allow/deny 表；反例测试
  照 coverage 套件惯例（每格至少一个 403 断言；先事实校验后主体校验的次序
  钉死）。
- **C OS ACL 恢复**：三账户 + 阶段 1 的两条机械断言（读不到 state.db、
  写不了 canonical）。
- **D 协作者与 secret 最小面**：help-<n> 分支白名单 + read_secret scope。

验收尺：`DOC-AUTH-001`（本报告使该 traceability 行有落点；实现后升
"✅ 有断言"）。

## 6. 未证实与边界（如实）

- Paperclip 的授权**打分器**（`authorization.ts` 核心裁决逻辑、角色判定、
  scope 评估）只核读了入口与 grants schema，**未逐行**读完整实现；本报告的
  "可借鉴"三条基于入口形态与 schema，不依赖打分器细节。
- R10 矩阵中 `pin/unpin`、`delete_worktree` 两行在正文他处无对应实现面
  （规划差异或未来卡），本报告如实标注而非硬造映射。
- 本报告不构成 ADR；任何"决定"（如键命名法采纳与否）由认证任务开工时按
  仓库惯例（ADR-DEV-00N）落记录。
