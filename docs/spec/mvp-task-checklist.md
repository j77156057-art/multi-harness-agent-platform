# 多 Harness Agent 协作平台 MVP 分工清单（重排版）

版本：0.4-replan
基线：`agent-collaboration-platform-design-v0.3-R10.md`（R10 终审修订规范）
上一版：`agent-collaboration-platform-mvp-task-checklist.md`（0.3，依赖图不可执行）
状态：可开工

## 0. 本次重排做了什么

0.3 版把 11 个任务按"功能模块"切分，第一轮建议分给 6 个 AI 并行。**这个并行是假的** ——
按 0.3 自己写的依赖，Day 1 真正能开工的只有 MVP-01，其余 5 个全部阻塞。本版做三件事：

1. **修正依赖图**：按真实依赖重排，并区分"硬依赖"（必须等 schema）与"软依赖"（可提前做）；
2. **区分可提前启动的准备工作**：把每个被阻塞任务拆出"不依赖 schema 的部分"，让其他 AI
   在等待期有事可做，而不是空转；
3. **补两条硬约束**（0.3 缺失，会直接导致 R9 那类"两份真相源"问题）：
   - 状态机与事件名的唯一真相源是 R10 §10 / §37.7，MVP 任务只能做**显式投影**；
   - 每个 MVP 任务必须带 `test_id`，与 R10 §26 的检查 ID 体系对齐。

### 0.1 依赖图（真实版）

~~~text
                    MVP-01 骨架与约定
                          |
                +---------+---------+
                |                   |
          MVP-02 schema        MVP-03b 迁移表投影 + docstate
                |                   |
                +---------+---------+
                          |
                    MVP-04 Claim/Lease/fencing
                          |
                +---------+---------+
                |                   |
          MVP-05 worktree        MVP-08 REST API
                |                   |
                |              MVP-10a Fake Harness
                |                   |
          MVP-06 Verify         MVP-10b 真实 Adapter
                |                   |
                +---------+---------+
                          |
                    MVP-07 Merge Gate
                          |
                +---------+---------+
                |                   |
          MVP-09 前端          MVP-11 集成测试与安全审查
~~~

### 0.2 关键路径

~~~text
MVP-01 -> MVP-02 -> MVP-04 -> MVP-05 -> MVP-06 -> MVP-07 -> MVP-11
                  \-> MVP-08 -> MVP-10a -> MVP-10b
~~~

关键路径长度 **7 个任务**，不是 0.3 版暗示的"6 AI 并行一轮搞定"。**MVP-04 是瓶颈**，
MVP-02 和 MVP-08 之后所有任务都直接或间接依赖它。

---

## 1. 硬约束（0.3 版缺失，必须遵守）

### H-1 状态机唯一真相源

状态机与事件名的唯一真相源是 `0.3-R10.md`：

- 状态集合：R10 §10 的 24 个正式状态；
- 迁移边：R10 §10 关键迁移表（R10 已把 `next` 全部单值化，86 条边）；
- 事件名：R10 §37.7 的 M（迁移，74）/ O（观测，13）/ R（恢复，1）三集合，共 88 项；
- 封闭规则：R10 §37.7 的七条。

0.3 版 MVP-03 自己列了 14 个状态，与 R10 的 24 个不一致（缺 `ready_waiting_deps`、
`queued`、`stale`、`reclaiming`、`plan_drift`、`impl_drift`、`awaiting_human`、
`draft_rejected` 等）。**如果 MVP-03 自造一套，R10 的 2403 行当场变成死文档。**

规则：
- MVP-03 只能实现 R10 §10 的**显式投影**，必须在 `docs/state-projection.md` 列出
  被省略的状态及省略理由；
- 任何新增状态或事件必须先改 R10 正文并升 schema_version，不能在代码里加；
- `tools/docstate`（见 H-2）在 CI 里强制这条。

### H-2 规范检查器从第一天就在

把 R10 复审用的 `r10_selfcheck.py`（43 项断言，已全绿）移植进仓库作为 `tools/docstate`，
作为 MVP-01 的强制交付物。理由：R10 复审证明"文档声称闭环"和"正文实际自洽"是两件事，
R9 的 10 条"已闭环"修订全部真实落地，但 R9 自身仍有 6 处机械缺口 —— 只有脚本能发现。

允许修改的文件：`tools/docstate/**`、`docs/spec/**`（R10 正文的只读副本或链接）。
禁止修改：R10 正文本身（改动必须走复审流程）。

### H-3 技术栈偏离必须记 ADR

R10 §5 规定后端 Go + React/TS/Vite。MVP 决定用 **Python FastAPI + Vue3**，理由：

- MVP 的验证目标是协调语义（不重复领取、状态可恢复、验证后合并），语言不影响正确性；
- Python `sqlite3` 在 Windows 上无 cgo 依赖，`BEGIN IMMEDIATE` / 条件 UPDATE /
  `RowsAffected` 语义与 Go 驱动一致，但测试代码量小得多；
- 团队现有主力栈是 FastAPI + Vue3。

**必须写 `docs/adr/ADR-DEV-001-stack-deviation.md`** 记录偏离与回退条件。
禁止默默改 R10 §5。

### H-4 每个任务带 test_id

每个 MVP 任务的完成标准必须引用 R10 §26 的检查 ID 或新增 `MVP-*` 测试 ID，
并在 `TASKS.md` 登记。禁止用"功能可用"作为完成标准。

---

## 2. 任务清单

状态图例：`ready` 现在可开工 / `waiting` 等硬依赖 / `prep` 可做准备工作

### MVP-01：项目骨架与规范绑定（AI-1）

状态：`ready`
依赖：无
唯一无依赖任务，Day 1 唯一可开工项

允许修改：`backend/**`、`frontend/**`、`docs/**`、`tools/**`、`.github/**`、根配置
禁止修改：`docs/spec/0.3-R10.md`（只读引用）

交付：

- 目录：`backend/`（FastAPI）、`frontend/`（Vue3）、`tests/`、`docs/`、`tools/docstate/`；
- `README.md`：新 AI 按它能启动项目并跑通一个最小测试；
- `docs/dev-convention.md`：分支命名、commit 格式、目录边界、允许/禁止修改清单；
- `docs/adr/ADR-DEV-001-stack-deviation.md`：H-3 的偏离记录；
- `docs/state-projection.md`：R10 §10 → MVP 状态的投影声明（H-1）；
- 统一 lint / format / test 命令（`make lint` / `make fmt` / `make test` 或等价物）；
- `TASKS.md`：任务登记表，含 `test_id` 列；
- `tools/docstate`：移植 `r10_selfcheck.py` 的 43 项断言，接入 CI。

完成标准：

- 新 AI 只看 README 能启动后端、跑一个最小测试；
- `tools/docstate` 对 R10 文档跑出 0 失败；
- `TASKS.md` 已登记 MVP-02～MVP-11 的 task_id、依赖、允许修改目录、test_id。

test_id：`DOC-STATE-001`、`DOC-TRACE-001`（本任务自身）

---

### MVP-02：SQLite schema 与事件事实源（AI-2）

状态：`waiting`（硬依赖 MVP-01）
可提前做：`prep` —— 写 schema 草案 DDL（不进仓库，放自己的分支或 notes）、
写事件 seq 分配器的算法说明、写"删投影后能否重建状态"的验证用例设计

交付：

- SQLite WAL 初始化、`busy_timeout`、migrations（可重复执行）；
- 表：`projects`、`groups`、`harnesses`、`tasks`、`task_attempts`、`task_events`、
  `task_claims`、`claim_requests`、`command_idempotency`、`resource_leases`、
  `side_effect_intents`、`workspaces`、`artifacts`、`reviews`、`verify_jobs`、
  `verification_receipts`、`schema_migrations`；
- 事件 `seq` / `created_at` / `actor` / `payload_hash` / `schema_version`；
- 事件只追加接口 + 基础查询接口；
- `tasks.attempt_no`、`tasks.hotfix`、`tasks.budget_override_by` 三个字段
  （R10 §8，缺任何一个都会让 MVP-04 的条件 UPDATE 编译失败）。

完成标准：

- 删除查询投影后可只凭事件重建任务状态；
- migrations 可重复执行且幂等；
- `claim_requests` 有 `unique(claim_request_id, task_id)`（R10 §8 / R9-03）。

test_id：`DOC-SCHEMA-001`、`CORE-RECOVER-001`

---

### MVP-03：状态机与 Plan 校验（AI-3）

状态：`waiting`（硬依赖 MVP-02）
可提前做：`prep` —— 移植迁移表投影、确定 MVP 省略哪些状态及理由、写 DAG 环检测用例

**注意**：0.3 版把本任务和 MVP-02 并列在第一组，但它硬依赖 MVP-02 的 schema。
真实位置在 MVP-02 之后。

交付：

- 实现 R10 §10 的显式投影（H-1），未定义迁移一律拒绝；
- 任务依赖 DAG + 环检测；
- 任务字段校验（引用 R10 §8 的必填字段）；
- 状态迁移时写 `task_events`；
- **`ready_waiting_deps` 必须实现**（R10 §10 给了 3 条出边：
  `dependencies_satisfied` / `block_timeout` / `orphan_detected`）。
  R10 复审发现 R9 缺后两条会导致上游 cancelled 时任务永久卡死 —— 这是 P1 修复项，
  不实现等于把 R10-05 的修复扔掉。

完成标准：

- 非法迁移被拒绝并告警；
- 环形依赖被拒绝；
- 所有非终态至少有一条恢复出口（由 `tools/docstate` 验证）；
- `next` 语义与 R10 §37.7 封闭规则一致：单值、无"或"、无圆括号。

test_id：`DOC-STATE-001`、`DOC-PLAN-001`

---

### MVP-04：原子 Claim、Attempt、Lease、Heartbeat（AI-4）★瓶颈

状态：`waiting`（硬依赖 MVP-02、MVP-03）
可提前做：`prep` —— 写并发 Claim 测试用例、写 `BEGIN IMMEDIATE` 事务顺序说明、
准备 Lease 巡检器的触发条件表

交付：

- Claim API；`claim_request_id` 请求级 + 任务项级双层幂等；
- `BEGIN IMMEDIATE` 写事务（禁止默认 DEFERRED）；
- 条件 UPDATE + `RowsAffected == 1` 断言，guard 含
  `attempt_no < max_attempts OR budget_override_by IS NOT NULL`（R10 §8 / §11）；
- `attempt_no`、`owner`、`fencing_epoch`（`current_epoch` 递增）；
- Lease 创建 / 续期 / 过期回收；
- 旧 epoch 写入被拒；
- 两个真实 SQLite 连接并发抢同一任务的测试。

完成标准：

- 两个真实连接同时 Claim 同一任务，只有一个成功（唯一 owner/attempt/Lease）；
- 重复 `claim_request_id` 返回首次结果，批量时**按任务项**返回首次结果
  （R10 §8 `claim_requests` 的 `unique(claim_request_id, task_id)`，漏了这条批量重试语义落空）；
- 旧 epoch 的 publish/heartbeat 在 API 层被拒。

test_id：`CORE-CLAIM-001`、`CORE-CLAIM-002`、`CORE-LEASE-001`、`CORE-FENCE-001`

---

### MVP-05：Git worktree 与发布代理（AI-5）

状态：`waiting`（硬依赖 MVP-03、MVP-04）
可提前做：`prep` —— 定 worktree 物理目录短 ID 方案、写 Windows longpaths 与 Defender
锁文件的踩坑预案、写分支命名口径

交付：

- 每个 attempt 独立 worktree + 任务分支（两个任务不得共享可写目录）；
- 分支命名：`agent/<group-short>/<harness>/<task-id>/<attempt-id>`
  （R10 §14；help 分支为 `help-<n>`，R10-09 要求 §14/§17/Push Proxy 三处口径一致）；
- `publish_task_branch` API，发布前校验 task / attempt / commit / fencing epoch / 分支白名单；
- `side_effect_intents(type=push)` 记录；
- worktree 回收与归档。

完成标准：

- 两个任务不共享可写 worktree；
- 旧 epoch 发布失败；
- 直接写集成分支的测试失败。

test_id：`GIT-ACL-001`、`GIT-ACL-002`

**MVP 简化**：R10 的三账户 ACL（`agent-scheduler` / `agent-harness-<id>` / `agent-verify`）
在 MVP 阶段降级为单账户 + `--insecure-single-account`，但必须写明这是临时措施，
以及阶段 2 前必须补齐的三账户清单。

---

### MVP-06：Verify Worker 与完成证据（AI-6）

状态：`waiting`（硬依赖 MVP-05）
可提前做：`prep` —— 设计 verify profile 结构、写 baseline differential 的用例、
写 overlay 回收策略

交付：

- `submit_result` API；
- 在精确 `head_commit` 上跑测试（fresh detached worktree）；
- `verification_receipts`，append-only，unique `(task_id, attempt_id, commit, verify_profile)`；
- receipt 记录：commit / base_commit / baseline_exit_code / head_exit_code /
  verify_profile / fencing_epoch / 日志 hash / failure_reason；
- `failure_reason` 区分 `code_failed` / `env_failed` / `timeout` / `aborted`
  （R10 §28 不变量，混用会导致环境失败被当代码失败返工）；
- `differential: not_applicable` 路径：`verifying -> awaiting_human`（正向边，R10-fix-03 补的）
  与 `awaiting_human -> verifying`（反向边）**两条都要实现**；
- overlay 回收；
- 生成 `completion.md` / `test-results.md` / `verification/receipt.json`。

完成标准：

- 测试未通过不能进入 Review；
- receipt 与待合并 commit 不一致时 Merge Gate 拒绝。

test_id：`VERIFY-DIFF-001`、`VERIFY-RECEIPT-001`

**MVP 简化**：baseline differential 的"base 必须失败 / head 必须通过"在 MVP 可先用
`differential: required` 单侧（head 必须通过），但必须把"baseline 也失败"的 flaky 判定
标为阶段 2 交付，不能静默跳过。

---

### MVP-07：Review、Merge Gate 与集成分支（AI-7）

状态：`waiting`（硬依赖 MVP-06）

交付：

- Reviewer Claim + Review Lease（独立于开发 Lease）；
- 禁止 owner 审查自己的 attempt，同 harness 自审拒绝；
- `approve` / `request_changes`；
- Merge Gate 交叉断言：`receipt.commit == review.reviewed_commit == 待合并分支 head`、
  `receipt.fencing_epoch == task.current_epoch`、`receipt.base_commit == task.base_commit`；
- 只有 Merge Worker 能写集成分支；
- 合并失败区分 `merge_conflict`（可自动消解）/ `merge_conflict_needs_owner`
  （需 owner 重做）/ `merge_post_commit_failure`（走 revert recovery）；
- 简化 revert：机械断言 `tree(revert_commit) == tree(first_parent(failed_merge_commit))`。

完成标准：

- 未经 Verify、Review 或 commit 不匹配的代码不能进入集成分支；
- 冲突三条分支的 next 与 R10 §10 单值一致，不出现"approved 或 changes_requested"。

test_id：`MERGE-EXACT-001`、`MERGE-REVERT-001`、`MERGE-REVERT-002`

---

### MVP-08：REST API、群聊与消息事件（AI-8）

状态：`waiting`（硬依赖 MVP-02、MVP-03）
可提前做：`prep` —— 写 OpenAPI 契约草案、写错误码表、写幂等响应约定
（**这个 prep 可以在 MVP-02 落地前就做完**，是本版最重要的解阻塞建议）

交付：

- 创建项目 / 群组 / 方案 / 任务；
- 查询任务与事件（支持 `since_seq` 增量）；
- Claim / Heartbeat / Submit / Help / Release / Abandon / Request Changes；
- 群聊消息 + @mention + 消息与 `task_events` 关联；
- 只绑定 localhost；
- 统一错误码与幂等响应。

完成标准：

- 只通过 API 能跑完"建群 -> 发方案 -> 领取 -> 提交 -> 审查"。

test_id：`DOC-TRACE-001`（追溯矩阵）

**绑定提示**：API 的路径命名应与 R10 §7 Adapter 接口对齐
（`claim` / `heartbeat` / `submit_result` / `publish_task_branch` 等），
否则 MVP-10 的 Adapter 要做一层无意义的转换。

---

### MVP-09：最小前端（AI-9）

状态：`waiting`（硬依赖 MVP-08）

交付：项目/群组列表、任务看板、任务详情、Claim/Release/Help/Submit 操作、
群聊与系统事件、Review 与 Merge Gate 页面、显示 owner/attempt/Lease/epoch/验证结果/commit。

完成标准：人类不直接访问数据库即可完成一条完整任务链路；前端不能绕过 API 改权威字段。

test_id：新增 `MVP-UI-001`

**依赖现实**：本任务在关键路径之外。若要压缩时间轴，可**整体后置**到 MVP-11 之后，
先用一个 CLI 脚本代替人工操作 —— 因为 MVP 的验证目标是协调语义，不是界面。

---

### MVP-10：Harness Adapter（AI-10）

状态：`waiting`
拆分：`10a` 依赖 MVP-08；`10b` 依赖 10a + MVP-05/06/07 稳定

交付：

- **10a**：Fake Harness（自动 Claim / Heartbeat / 提交成功或失败结果）+ 统一 Adapter 接口
  + 能力探测与注册版本 + 超时/放弃/重新接手场景；
- **10b**：Codex Adapter、Claude Code Adapter、OpenCode Adapter。

完成标准：

- Fake Harness 完整跑通（这是 MVP 的主验证路径，优先级高于真实 Adapter）；
- 至少一个真实 Harness 完成 Claim / Heartbeat / Submit / Review。

test_id：新增 `MVP-ADAPTER-001`、`MVP-ADAPTER-002`

**优先级说明**：如果时间紧，10b 可以整体砍掉。Fake Harness 跑通就足以验证平台自身
的协作能力 —— 真实 Adapter 只证明接入，不证明内核。

---

### MVP-11：集成测试与安全审查（AI-11）

状态：`waiting`（硬依赖 MVP-04～MVP-10）

交付：并发 Claim、重复 Claim、Lease 过期恢复、旧 epoch 拒绝、Verify/Review/Merge 顺序、
直接 push 主分支拒绝、secret 扫描先于事件落库、Windows 路径与长路径与 worktree 清理测试；
生成 MVP 终审报告。

完成标准：核心流程自动化通过；所有失败都有 `task_id`、`attempt_id`、`event seq` 和复现步骤。

test_id：汇总 MVP-04～MVP-10 全部

**与 R10 复审方法论对齐**：终审报告必须包含**机械校验脚本的输出**，
不能只写人工核对结论。R10 的经验是"人工判定的已闭环"和"实际自洽"差距巨大。

---

## 3. 波次安排（真实可执行）

### Day 1（今天）

| AI | 任务 | 说明 |
|---|---|---|
| AI-1 | MVP-01 | 唯一可开工 |

其余 5 个 AI **不要空等**，各自做对应任务的 `prep` 项（见上），产出放到
`docs/prep/<task-id>/`，不进主分支。

### Day 2 起

MVP-01 合入主干后，AI-2 进 MVP-02；AI-3 同步做迁移表投影（此时 schema 未定，
但状态投影可先写）。MVP-08 的 API 契约草案（prep）可以提前完成，schema 一落地就能实现。

### 关键路径

MVP-01 → MVP-02 → MVP-04 → MVP-05 → MVP-06 → MVP-07 → MVP-11

MVP-04 是瓶颈：它同时挡住 MVP-05/06/07/11 四条线。**建议给 MVP-04 配 2 个 AI**，
一人做 Claim/幂等、一人做 Lease/fencing/巡检器，在 MVP-02 落地后立即并行。

---

## 4. 暂时不要分配的工作

0.3 版的 9 条继续保持，另外加两条：

- 多机调度；
- PostgreSQL、Redis、Docker 集群；
- 六个 Harness 同时接入；
- 复杂费用结算；
- GUI 自动化控制 TRAE / WorkBuddy / Qoder；
- 完整 RBAC 和企业级身份系统；
- 自研大模型 Planner；
- 复杂 flaky 自动放行（`baseline 也失败` 的放行判定放阶段 2）；
- **把 R10 全部字段一次性实现**（只实现 MVP 需要的投影）；
- **R10 §37.4 声称的 AO 能力改造**（见下）。

---

## 5. 开工前必须解决的 AO 前提偏差

R10 §3、§37.4 把 Agent Orchestrator 的三个能力当作 `ao_managed_git` 宿主模式的前提：

| R10 声称的 AO 能力 | R10 的用途 | 实际源码核对结果 |
|---|---|---|
| `head_check` / `dev_headcheck` | Verify baseline differential 的基础 | **不存在**（全库无 `head_check` / `headcheck`） |
| `fencing_token` | 迁移为 `task_attempts.current_epoch` + `boot_id` | **不存在**该字段（只有泛用注释里的 "fencing"） |
| `lanes` | 由调度中心 `resource_leases` 取代或镜像 | **不存在**（全库无 `lane`） |

另外：AO 后端是 **Go**（`backend/go.mod`、`internal/`、`pkg/`），与 R10 假设的形态不同；
且该源码副本**不含 `.git`**（下载快照，无法追版本）。

影响与处置：

1. `ao_managed_git` 宿主模式在 MVP 阶段**不可用**，R10 §3 已有的第三档 `patch_apply`
   是唯一可行的 AO 路径 —— 这恰好是 R9-05 / R10-11 已经补全的那一档；
2. R10 §37.4 的"O 能力对照表"交付物（阶段 0）需要按实际源码重写，不能照抄 R10 描述；
3. 阶段 0 的 AO probe 结论应直接写 `ao_managed_git = no-go`；
4. 需要一份 `docs/ao-capability-audit.md` 记录本次核对，作为 R10 的实现前偏差。

**这一条不解决就开工，MVP-10 的 Adapter 会按不存在的接口去实现。**

---

## 6. 每个 AI 的统一任务卡模板

~~~text
任务编号：MVP-__
负责人 Harness：
开始时间：
依赖任务：
真相源引用：R10 §__（状态机/事件/schema 必须引用，不得自造）
允许修改目录：
禁止修改目录：
交付分支：agent/<harness>/<task-id>
完成标准：
test_id：
测试命令：
当前状态：ready / waiting / prep / claimed / in_progress / verifying / awaiting_review / merged / blocked / abandoned
完成时间：
commit：
验证结果：
已知问题：
~~~

新增两个必填字段：

- **真相源引用**：写明本任务依赖 R10 的哪几节。空白视为任务卡不完整，任务分配无效；
- **test_id**：没有 test_id 的完成标准视为不可验收。
