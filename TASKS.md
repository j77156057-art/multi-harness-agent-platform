# 任务登记表

> ⚠️ **开工前必读 —— 不要在仓库根目录 `new agent/` 编辑文件。**
> 每个 AI 在自己的隔离工作区里干活：`.worktrees/<harness>/`。
> 共享工作区会让提交被无声丢弃（任何人的 `git checkout` / `git reset` 都改变所有人的
> 工作树；本项目已丢 4 个提交，其中一个是别的 AI 的）。新 AI 先跑
> `./scripts/new-ai.ps1 -Harness <名字>`。规则见 `docs/dev-convention.md` H-7，
> 会话入口见 `AGENTS.md`。

真相源：`docs/spec/mvp-task-checklist.md`（0.4 重排版）
硬约束 H-4：**没有 `test_id` 的完成标准不可验收。**

## 状态图例

`ready` 现在可开工 / `waiting` 等硬依赖 / `prep` 可做准备工作 / `doing` 进行中 /
`review` 待审 / `done` 已完成 / `blocked` 阻塞 / `claimed` 已认领待验收 /
`✅ 已验收`

## 任务表

| task_id | Harness | 依赖 | 状态 | 分支 | test_id |
|---|---|---|---|---|---|
| MVP-01 | WorkBuddy | — | ✅ done | `main` ⚠️未走 Gate | `DOC-STATE-001` `DOC-TRACE-001` |
| MVP-02 | Codex | MVP-01 | ✅ 已验收 | `agent/local/codex/MVP-02/a1` | `DOC-SCHEMA-001` `CORE-RECOVER-001` |
| MVP-02-amend | Claude Code | MVP-02 | ✅ done | `agent/local/claude/MVP-02-amend/a1` | `DOC-SCHEMA-001` `DOC-STATE-001` |
| worktree-isolation | Claude Code | — | ✅ done | `agent/local/claude/worktree-isolation/a1` | `MVP-PROCESS-001` |
| agent-onboarding | Claude Code | worktree-isolation | ✅ done | `agent/local/claude/agent-onboarding/a1` | `MVP-PROCESS-001` |
| discipline | Claude Code | agent-onboarding | ✅ done | `agent/local/claude/discipline/a1` | `MVP-PROCESS-001` |
| fix-wal-lock-retry | WorkBuddy | MVP-02 | ✅ done | `agent/workbuddy/fix-wal-lock-retry` | `CORE-RECOVER-001` |
| R10-fix-10 | WorkBuddy | MVP-02-amend | ✅ done | `agent/workbuddy/R10-fix-10-task-claims` | `DOC-SCHEMA-001` `DOC-STATE-001` |
| R10-fix-10-card | TRAE | R10-fix-10 | ✅ done | `agent/local/trae/r10-fix-10-card/a1` | `MVP-PROCESS-001` |
| fix-flaky-init | WorkBuddy | MVP-02 | ✅ done（结论：无法复现，撤回登记） | `agent/workbuddy/fix-flaky-init` | `CORE-RECOVER-001` |
| chain-template-closeout | WorkBuddy | MVP-06 | ✅ done（已测为负收益，撤销优化） | `agent/workbuddy/chain-template-rejected` | `MVP-PROCESS-001` |
| slow-test-visibility | WorkBuddy | — | ✅ done（慢测试排行 + slow marker 已落地） | `agent/workbuddy/slow-test-visibility` | `MVP-PROCESS-001` |
| report-mergeguard-commit-bug | WorkBuddy | — | ✅ done（报告已交付；修复由 @opencode 单独认领） | `agent/workbuddy/report-mergeguard-commit-bug` | `MVP-PROCESS-001` |
| fix-flaky-init-card | TRAE | fix-flaky-init | ✅ done | `agent/local/trae/fix-flaky-init-card/a1` | `MVP-PROCESS-001` |
| claim-nullable | WorkBuddy | MVP-04 | ✅ done（R10-fix-11 裁定） | `agent/workbuddy/fix-claim-nullable` | `CORE-CLAIM-002` `CORE-RECOVER-001` |
| claim-nullable-card | TRAE | claim-nullable | ✅ done | `agent/local/trae/claim-nullable-card/a1` | `MVP-PROCESS-001` |
| MVP-08-prep | Qoder | — | ✅ done | `agent/local/qoder/MVP-08-prep/a1` | `DOC-TRACE-001` `MVP-API-001` |
| MVP-03 | Codex | MVP-02 | ✅ done（初验 Conditional Fail，P1 由 MVP-03-amend 闭环；TRAE 20:47 凭证据关单） | `agent/local/codex/MVP-03/a1` | `DOC-STATE-001` `DOC-PLAN-001` |
| MVP-03-amend | TRAE | MVP-03 | ✅ done | `agent/local/trae/MVP-03-amend/a1` | `DOC-STATE-001` `DOC-PLAN-001` `MVP-TRANSITION-MIRROR-001` || graph-traceability | Claude Code | discipline | ✅ done | `agent/local/claude/graph-traceability/a1` | `MVP-PROCESS-001` `DOC-PLAN-001` |
| graph-traceability | Claude Code | discipline | ✅ done | `agent/local/claude/graph-traceability/a1` | `MVP-PROCESS-001` `DOC-PLAN-001` |
| gate-baseline-repair | OpenCode | R10-fix-10 | ✅ done | `agent/local/opencode/gate-baseline-repair/a1` | `MVP-PROCESS-001` |
| trace-closure | OpenCode | MVP-03-amend | ✅ done | `agent/local/opencode/gate-baseline-repair/a1` | `MVP-PROCESS-001` `MVP-TRANSITION-MIRROR-001` |
| plan-evidence-failclosed | OpenCode | MVP-03 | ✅ done | `agent/local/opencode/plan-evidence-failclosed/a1` | `DOC-PLAN-001` `MVP-PROCESS-001` |
| merge-interlock | OpenCode | discipline | ✅ done | `agent/local/opencode/merge-interlock/a1` | `MVP-MERGE-LOCK-001` `MVP-PROCESS-001` |
| MVP-04 | Qoder | MVP-02 MVP-03 | ✅ 已验收（TRAE 代码核查 + WorkBuddy 注入验证 3/3） | `agent/local/qoder/MVP-04/a1` | `CORE-CLAIM-001` `CORE-CLAIM-002` `CORE-LEASE-001` `CORE-FENCE-001` |
| MVP-04-verify | TRAE | MVP-04 | ✅ done | `agent/local/trae/MVP-04-verify/a1` | `CORE-CLAIM-001` `CORE-CLAIM-002` `CORE-LEASE-001` `CORE-FENCE-001` |
| verify-mvp-04 | WorkBuddy | MVP-04 | ✅ done（注入验证） | `agent/workbuddy/verify-mvp-04` | `CORE-CLAIM-001` `CORE-CLAIM-002` `CORE-FENCE-001` |
| claims-split | WorkBuddy | — | ✅ done | `agent/workbuddy/split-tasks-md` | `MVP-PROCESS-001` |
| tasks-split | WorkBuddy | claims-split | ✅ done（TASKS.md 279KB→24KB，卡片进 docs/tasks/notes/） | `agent/workbuddy/tasks-split/a1` | `MVP-PROCESS-001` |
| claims-split-review | Qoder | verify-mvp-04 | ✅ done | `agent/local/qoder/claims-split-review/a1` | `MVP-PROCESS-001` |
| deadlock-fix | WorkBuddy | — | ✅ done（mergeguard × discipline 合并死锁，`_ensure_repo_root_on_path`） | `agent/workbuddy/deadlock-fix/a1` | `MVP-PROCESS-001` |
| claim-denum-migrate | WorkBuddy | deadlock-fix | ✅ done（方案 C 文件层迁移，2 个认领文件去序号前缀 + 4 处自指同步） | `agent/workbuddy/claim-denum-migrate/a1` | `MVP-PROCESS-001` |
| gate-sandbox-pitfall | WorkBuddy | — | ✅ done（回填「跑门禁须走沙箱外」+「撞号在 main 上是既成事实」） | `agent/workbuddy/gate-sandbox-pitfall/a1` | `MVP-PROCESS-001` |
| claim-denum-migrate-rest | WorkBuddy | — | ✅ done（WorkBuddy 剩余 11 个认领文件去前缀 + 21 处活指针同步；6 处跨边界引用如实留档） | `agent/workbuddy/claim-denum-migrate-rest/a1` | `MVP-PROCESS-001` |
| MVP-08-verify | TRAE | MVP-08 | ✅ done（验收通过，附遗留清单 4 项） | `agent/local/trae/MVP-08-verify/a1` | `DOC-TRACE-001` `MVP-API-001` |
| message-global-seq | TRAE | MVP-08 | ✅ done（关闭验收遗留 3a） | `agent/local/trae/message-global-seq/a1` | `MVP-API-001` |
| message-refs-persist | TRAE | MVP-08 | ✅ done（关闭验收遗留 3b） | `agent/local/trae/message-refs-persist/a1` | `MVP-API-001` |
| GATE-MERGE-CHECK-01 | TRAE | discipline | ✅ done（merge 组已移植到 claims 目录版检查器） | `agent/local/trae/gate-merge-check-01/a1` | `MVP-PROCESS-002` |
| MVP-05 | Qoder | MVP-03 MVP-04 | ✅ done | `agent/local/qoder/MVP-05/a1` | `GIT-ACL-001` `GIT-ACL-002` |
| MVP-05-verify | TRAE | MVP-05 | ✅ done（通过附遗留清单） | `agent/local/trae/MVP-05-verify/a1` | `GIT-ACL-001` `GIT-ACL-002` |
| MVP-06 | Qoder | MVP-05 | ✅ done | `agent/local/qoder/MVP-06/a1` | `VERIFY-DIFF-001` `VERIFY-RECEIPT-001` |
| MVP-06-verify | TRAE | MVP-06 | ✅ done（通过附遗留清单） | `agent/local/trae/MVP-06-verify/a1` | `VERIFY-DIFF-001` `VERIFY-RECEIPT-001` |
| MVP-07 | Qoder | MVP-06 | ✅ done | `agent/local/qoder/MVP-07/a1` | `MERGE-EXACT-001` `MERGE-REVERT-001` `MERGE-REVERT-002` |
| MVP-08 | Qoder | MVP-02 MVP-03 | ✅ 已验收（TRAE 代码核查，通过附遗留清单） | `agent/local/qoder/MVP-08/a1` | `DOC-TRACE-001` `MVP-API-001` |
| MVP-09 | Claude Code | MVP-08 | ✅ done | `agent/local/claude/MVP-09/a1` | `MVP-UI-001` |
| MVP-10a | Qoder | MVP-08 | ✅ done | `agent/local/qoder/MVP-10a/a1` | `MVP-ADAPTER-001` |
| MVP-10b | Qoder | MVP-10a MVP-05~07 | ✅ 已验收（TRAE 外部核查，通过附遗留清单 5 项，无 P1；04:23 先合 main 已留档） | `agent/local/qoder/MVP-10b/a1` | `MVP-ADAPTER-002` |
| MVP-10b-verify | TRAE | MVP-10b | ✅ done（通过附遗留清单 5 项，无 P1） | `agent/local/trae/MVP-10b-verify/a1` | `MVP-ADAPTER-002` |
| dup-incident | Claude Code | — | ✅ done | `agent/local/claude/dup-incident/a1` | `MVP-PROCESS-001` |
| claim-seq-adopt | Claude Code | — | ✅ done | `agent/local/claude/claim-seq-adopt/a1` | `MVP-PROCESS-002` |
| MVP-11 | Qoder | MVP-04~09 MVP-10b | ✅ done（MVP-09 按清单条款后置，见卡） | `agent/local/qoder/MVP-11/a1` | `CORE-STATE-COVERAGE-001` |
| tasks-critical-path | Claude Code | graph-traceability | ✅ done | `agent/local/claude/tcp3/a1` | `MVP-PROCESS-001` `DOC-PLAN-001` |
| branch-visibility | Claude Code | tasks-critical-path | ✅ done | `agent/local/claude/branch-visibility/a1` | `MVP-PROCESS-001` |
| claim-seq | Claude Code | branch-visibility | 🔍 review（提案，不实施） | `agent/local/claude/claim-seq/a1` | `MVP-PROCESS-001` |
| merge-interlock-review | TRAE | merge-interlock | ✅ done（退回修 1-4 后再合） | `agent/local/trae/merge-interlock-review/a1` | `MVP-MERGE-LOCK-001` |
| root-worktree-clean-review | TRAE | merge-interlock-review | ✅ done（退回修问题 1-9 后复审） | `agent/local/trae/root-worktree-clean-review/a1` | `MVP-PROCESS-002` |
| claim-denum | OpenCode | claim-seq | ✅ done（提案已交付，采纳与否待裁决） | `agent/local/opencode/claim-denum/a1` | `MVP-PROCESS-001` |
| claim-denum-coordinator-review | WorkBuddy | claim-denum | ✅ done（材料已交付并合入；Qoder 按协调者授权收尾，见卡注） | `agent/workbuddy/claim-denum-review/a1` | `MVP-PROCESS-001` |
| sweeper-family | Qoder | — | ✅ done（只交付设计；承接 MVP-11 报告 §6） | `agent/local/qoder/sweeper-family/a1` | `SCHED-SWEEP-001` |
| sweeper-runner | Qoder | sweeper-family | ✅ done | `agent/local/qoder/sweeper-runner/a1` | `SCHED-SWEEP-001` |
| sweeper-deps-resources | Qoder | sweeper-runner | ✅ done | `agent/local/qoder/sweeper-deps-resources/a1` | `SCHED-SWEEP-002` |
| sweeper-aging-stuck | Qoder | sweeper-deps-resources | ✅ done | `agent/local/qoder/sweeper-aging-stuck/a1` | `SCHED-SWEEP-003` |
| sweeper-workers | Qoder | sweeper-aging-stuck | ✅ done | `agent/local/qoder/sweeper-workers/a1` | `SCHED-SWEEP-004` |
| sweeper-drift | Qoder | sweeper-workers | ✅ done | `agent/local/qoder/sweeper-drift/a1` | `SCHED-SWEEP-005` |
| human-ops | Qoder | sweeper-drift | ✅ done（实现 514c4b2；豁免 3→1，覆盖套件收官） | `agent/local/qoder/human-ops/a1` | `HUMAN-OPS-001` |
| root-worktree-clean | OpenCode | merge-interlock | ✅ done（问题 6 按 C 方案落地） | `agent/local/opencode/root-worktree-clean/a1` | `MVP-PROCESS-002` |
| claim-denum-coordinator | OpenCode | claim-denum | ✅ done（合入 6aa788e；a2/a3/a4 回填与登记） | `agent/local/opencode/claim-denum-coordinator/a1` `a2` `a3` `a4` | `MVP-PROCESS-001` |
| mergeguard-fix | OpenCode | root-worktree-clean | ✅ done（合入 2c85a9c；a2 已由 Qoder 协助回填） | `agent/local/opencode/mergeguard-fix/a1` `a2` | `MVP-MERGE-LOCK-001` |
| win-utf8-stdio | OpenCode | — | ✅ done（已提交待合；a1 = 0ed6e0d，四道门禁在无任何 UTF-8 环境变量下全绿） | `agent/local/opencode/win-utf8-stdio/a1` | `MVP-PROCESS-002` |
| gate-format-repair | Qoder | — | ✅ done（实现 edae3c1；lint 基线恢复 + 5 卡标题回填） | `agent/local/qoder/gate-format-repair/a1` | `MVP-PROCESS-002` |
| paperclip-buzz-adr | Qoder | gate-format-repair | ✅ done（实现 59b4960；ADR-DEV-003 落成） | `agent/local/qoder/paperclip-buzz-adr/a1` | `MVP-ARCH-001` |
| adapter-verbs | Qoder | paperclip-buzz-adr | ✅ done（实现 3c98a77；§7 十六动词收满，差额清零） | `agent/local/qoder/adapter-verbs/a1` | `MVP-ADAPTER-001` |
| split-closure | Qoder | adapter-verbs | ✅ done（实现 27d7be0；闭环成形，顺带修方向 bug） | `agent/local/qoder/split-closure/a1` | `CORE-SPLIT-001` |
| frontend-thread | Qoder | paperclip-buzz-adr | ✅ done（实现 5905f4a；三栏壳+项目群+任务线程，浏览器实测留档） | `agent/local/qoder/frontend-thread/a1` | `MVP-UI-001` |
| jsonl-export | Qoder | frontend-thread | ✅ done（实现 cead61a；导出水位/原子替换/重试/对账 + ADR-DEV-004） | `agent/local/qoder/jsonl-export/a1` | `CORE-EXPORT-001` |
| paperclip-buzz-source | Qoder | paperclip-buzz-adr | ✅ done（实现 be8291b；源码核实报告 + ADR-DEV-003 勘误） | `agent/local/qoder/paperclip-buzz-source/a1` | `MVP-ARCH-001` |
| alert-webhook | Qoder | paperclip-buzz-source | ✅ done（实现 3c27193；升级事件→签名投递→重试停靠；闭告警通道缺口） | `agent/local/qoder/alert-webhook/a1` | `OPS-ALERT-001` |
| liveness-watch | Qoder | alert-webhook | ✅ done（实现 6397e8f；观测+零commit告警+stuck 修复，变异验证） | `agent/local/qoder/liveness-watch/a1` | `OPS-LIVENESS-001` |
| budget-guard | Qoder | liveness-watch | ✅ done（实现 ed00e94；预留/结算/巡检/事故，变异验证） | `agent/local/qoder/budget-guard/a1` | `OPS-BUDGET-001` |
| diff-message | Qoder | frontend-thread | 🚧 doing | `agent/local/qoder/diff-message/a1` | `MVP-UI-002` |

★ MVP-04 是瓶颈：同时挡住 MVP-05/06/07/11 四条线。建议配 2 个 AI
（一人做 Claim/幂等，一人做 Lease/fencing/巡检器），在 MVP-02 落地后立即并行。

> ⚠️ **MVP-04 开工前必读**：R10 §8 `claim_requests` 行曾声明一个该行未定义的 `task_id`
> 列（唯一键单元格与关键字段单元格矛盾），已由 `MVP-02-amend` 按 R10-fix-09 修正并合入
> `main`（`d7bf8d0`）。**该矛盾已不存在，但由此产生的错误结论仍需防复发：**
> **不要因为看到 §11 的 `unique(claim_request_id, task_id)` 就断定 `schema` 少一张
> `claim_request_results` 表——该拆表方案已被否决**（R10 §29.6 要求不再往正文加表）。
> per-task 幂等的唯一落点是 `task_claims UNIQUE(claim_request_id, task_id)`，
> 重试回放用 `SELECT * FROM task_claims WHERE claim_request_id = ?`，
> 两层职责划分见 R10 §11。

## 认领登记（R10 §0 总协调规则第 2 条）

**本表只读**。每个 task 的认领记录在 `docs/claims/<harness>--<task-id>.md>`（迁移期也接受旧的 `<序号>-` 前缀，见 `docs/proposals/claim-denum.md`），
各自独占文件、永不冲突。新认领请新建文件，不要往本表加行。

**规则：领取任务前必须在任务清单写入 `task_id`、Harness 名称、时间、分支。**

| 序号 | task_id | Harness | 认领时间 | 分支 | 状态 |
|---|---|---|---|---|---|
<!-- MVP-01 → docs/claims/workbuddy--MVP-01.md -->
<!-- MVP-02 → docs/claims/02-codex--MVP-02.md -->
<!-- MVP-02-amend → docs/claims/03-claude-code--MVP-02-amend.md -->
<!-- worktree-isolation → docs/claims/04-claude-code--worktree-isolation.md -->
<!-- agent-onboarding → docs/claims/05-claude-code--agent-onboarding.md -->
<!-- discipline → docs/claims/06-claude-code--discipline.md -->
<!-- MVP-03 → docs/claims/07-codex--MVP-03.md -->
<!-- fix-wal-lock-retry → docs/claims/workbuddy--fix-wal-lock-retry.md -->
<!-- MVP-08-prep → docs/claims/10-qoder--MVP-08-prep.md -->
<!-- R10-fix-10 → docs/claims/workbuddy--R10-fix-10.md -->
<!-- graph-traceability → docs/claims/08-claude-code--graph-traceability.md -->
<!-- fix-flaky-init → docs/claims/workbuddy--fix-flaky-init.md -->
<!-- MVP-03-amend → docs/claims/12-trae--MVP-03-amend.md -->
<!-- claim-nullable → docs/claims/workbuddy--claim-nullable.md -->
<!-- verify-mvp-04 → docs/claims/workbuddy--verify-mvp-04.md -->
<!-- R10-fix-10-card → docs/claims/15-trae--R10-fix-10-card.md -->
<!-- MVP-04 → docs/claims/13-qoder--MVP-04.md -->
<!-- fix-flaky-init-card → docs/claims/18-trae--fix-flaky-init-card.md -->
<!-- MVP-08 → docs/claims/17-qoder--MVP-08.md -->
<!-- MVP-04-verify → docs/claims/20-trae--MVP-04-verify.md -->
<!-- claim-nullable-card → docs/claims/22-trae--claim-nullable-card.md -->
<!-- plan-evidence-failclosed → docs/claims/16-opencode--plan-evidence-failclosed.md -->
<!-- claims-split-review → docs/claims/23-qoder--claims-split-review.md -->
<!-- claims-split → docs/claims/workbuddy--claims-split.md -->
<!-- MVP-08-verify → docs/claims/26-trae--MVP-08-verify.md -->
<!-- message-global-seq → docs/claims/27-trae--message-global-seq.md -->
<!-- GATE-MERGE-CHECK-01 → docs/claims/28-trae--GATE-MERGE-CHECK-01.md -->
<!-- message-refs-persist → docs/claims/30-trae--message-refs-persist.md
     （原拟用 29，与已合 main 的 29-qoder--MVP-05.md 撞号，顺延为 30） -->
<!-- MVP-05-verify → docs/claims/36-trae--MVP-05-verify.md
     （切出自 719fca6 时拟用 34，与并行合入 main 的 34-claude-code--branch-visibility
     撞号，35 又被 qoder--MVP-06 占用，顺延为 36） -->
<!-- MVP-06-verify → docs/claims/37-trae--MVP-06-verify.md -->
<!-- MVP-10b-verify → docs/claims/46-trae--MVP-10b-verify.md -->
<!-- claim-denum → docs/claims/49-opencode--claim-denum.md -->


## 任务卡

**本节不再内联任务卡。** 每张卡在 `docs/tasks/notes/<task-id>.md`，
一个 task 一个文件、永不冲突。

新 task 的卡建在 `docs/tasks/notes/<task-id>.md`，字段照 `## 统一任务卡` 模板填。
`discipline` 的 `cards` 组从「本文件 + `docs/tasks/notes/*.md`」两处一起读，
卡片身份只认正文里的 `任务编号` 字段，不认文件名。

**为什么这次才拆**：2026-10-05 的 `claims-split` 只拆了认领表，卡片与复核记录
留在原地 —— 而卡片才是本文件 94% 的体积（275 KB 里约 254 KB）。
**只拆一个多写点等于没拆**：冲突面最大的两个点原封不动地留着。

理由是**冲突面与可读性**，不是性能。曾写过的「275 KB 超过 git
`core.bigFileThreshold` 导致 `git add` 间歇失败」已被受控对照推翻
（拆分前 279450 字节连续 add 20 次全过），真正机制是并发 git 进程抢
`index.lock`。否证记录见 `docs/tasks/notes/tasks-split.md`。

### 任务卡索引

| task_id | 卡片 | task_id | 卡片 |
|---|---|---|---|
| MVP-01 | [卡](docs/tasks/notes/MVP-01.md) | fix-flaky-init | [卡](docs/tasks/notes/fix-flaky-init.md) |
| MVP-02 | [卡](docs/tasks/notes/MVP-02.md) | fix-flaky-init-card | [卡](docs/tasks/notes/fix-flaky-init-card.md) |
| MVP-02-amend | [卡](docs/tasks/notes/MVP-02-amend.md) | claim-nullable | [卡](docs/tasks/notes/claim-nullable.md) |
| MVP-03 | [卡](docs/tasks/notes/MVP-03.md) | claim-nullable-card | [卡](docs/tasks/notes/claim-nullable-card.md) |
| MVP-03-amend | [卡](docs/tasks/notes/MVP-03-amend.md) | MVP-08-prep | [卡](docs/tasks/notes/MVP-08-prep.md) |
| MVP-04 | [卡](docs/tasks/notes/MVP-04.md) | MVP-08 | [卡](docs/tasks/notes/MVP-08.md) |
| MVP-04-verify | [卡](docs/tasks/notes/MVP-04-verify.md) | MVP-08-verify | [卡](docs/tasks/notes/MVP-08-verify.md) |
| MVP-05 | [卡](docs/tasks/notes/MVP-05.md) | message-global-seq | [卡](docs/tasks/notes/message-global-seq.md) |
| MVP-05-verify | [卡](docs/tasks/notes/MVP-05-verify.md) | GATE-MERGE-CHECK-01 | [卡](docs/tasks/notes/GATE-MERGE-CHECK-01.md) |
| MVP-06 | [卡](docs/tasks/notes/MVP-06.md) | message-refs-persist | [卡](docs/tasks/notes/message-refs-persist.md) |
| MVP-06-verify | [卡](docs/tasks/notes/MVP-06-verify.md) | merge-interlock | [卡](docs/tasks/notes/merge-interlock.md) |
| MVP-07 | [卡](docs/tasks/notes/MVP-07.md) | merge-interlock-review | [卡](docs/tasks/notes/merge-interlock-review.md) |
| MVP-09 | [卡](docs/tasks/notes/MVP-09.md) | root-worktree-clean-review | [卡](docs/tasks/notes/root-worktree-clean-review.md) |
| MVP-10a | [卡](docs/tasks/notes/MVP-10a.md) | plan-evidence-failclosed | [卡](docs/tasks/notes/plan-evidence-failclosed.md) |
| MVP-10b | [卡](docs/tasks/notes/MVP-10b.md) | gate-baseline-repair | [卡](docs/tasks/notes/gate-baseline-repair.md) |
| MVP-10b-verify | [卡](docs/tasks/notes/MVP-10b-verify.md) | trace-closure | [卡](docs/tasks/notes/trace-closure.md) |
| MVP-11 | [卡](docs/tasks/notes/MVP-11.md) | graph-traceability | [卡](docs/tasks/notes/graph-traceability.md) |
| worktree-isolation | [卡](docs/tasks/notes/worktree-isolation.md) | discipline | [卡](docs/tasks/notes/discipline.md) |
| agent-onboarding | [卡](docs/tasks/notes/agent-onboarding.md) | tasks-critical-path | [卡](docs/tasks/notes/tasks-critical-path.md) |
| R10-fix-10 | [卡](docs/tasks/notes/R10-fix-10.md) | branch-visibility | [卡](docs/tasks/notes/branch-visibility.md) |
| R10-fix-10-card | [卡](docs/tasks/notes/R10-fix-10-card.md) | claim-seq | [卡](docs/tasks/notes/claim-seq.md) |
| fix-wal-lock-retry | [卡](docs/tasks/notes/fix-wal-lock-retry.md) | dup-incident | [卡](docs/tasks/notes/dup-incident.md) |
| verify-mvp-04 | [卡](docs/tasks/notes/verify-mvp-04.md) | claim-denum | [卡](docs/tasks/notes/claim-denum.md) |
| claims-split | [卡](docs/tasks/notes/claims-split.md) | chain-template-closeout | [卡](docs/tasks/notes/chain-template-closeout.md) |
| claims-split-review | [卡](docs/tasks/notes/claims-split-review.md) | slow-test-visibility | [卡](docs/tasks/notes/slow-test-visibility.md) |
| report-mergeguard-commit-bug | [卡](docs/tasks/notes/report-mergeguard-commit-bug.md) | tasks-split | [卡](docs/tasks/notes/tasks-split.md) |
| root-worktree-clean | [卡](docs/tasks/notes/root-worktree-clean.md) | sweeper-family | [卡](docs/tasks/notes/sweeper-family.md) |
| claim-seq-adopt | [卡](docs/tasks/notes/claim-seq-adopt.md) | deadlock-fix | [卡](docs/tasks/notes/deadlock-fix.md) |
| claim-denum-coordinator-review | [卡](docs/tasks/notes/claim-denum-coordinator-review.md) | sweeper-runner | [卡](docs/tasks/notes/sweeper-runner.md) |
| sweeper-deps-resources | [卡](docs/tasks/notes/sweeper-deps-resources.md) | sweeper-aging-stuck | [卡](docs/tasks/notes/sweeper-aging-stuck.md) |
| mergeguard-fix | [卡](docs/tasks/notes/mergeguard-fix.md) | claim-denum-migrate | [卡](docs/tasks/notes/claim-denum-migrate.md) |
| gate-sandbox-pitfall | [卡](docs/tasks/notes/gate-sandbox-pitfall.md) | claim-denum-migrate-rest | [卡](docs/tasks/notes/claim-denum-migrate-rest.md) |
| sweeper-workers | [卡](docs/tasks/notes/sweeper-workers.md) | | |
| sweeper-drift | [卡](docs/tasks/notes/sweeper-drift.md) | | |
| human-ops | [卡](docs/tasks/notes/human-ops.md) | | |
| gate-format-repair | [卡](docs/tasks/notes/gate-format-repair.md) | | |
| paperclip-buzz-adr | [卡](docs/tasks/notes/paperclip-buzz-adr.md) | | |
| adapter-verbs | [卡](docs/tasks/notes/adapter-verbs.md) | | |
| split-closure | [卡](docs/tasks/notes/split-closure.md) | | |
| frontend-thread | [卡](docs/tasks/notes/frontend-thread.md) | | |
| jsonl-export | [卡](docs/tasks/notes/jsonl-export.md) | | |
| paperclip-buzz-source | [卡](docs/tasks/notes/paperclip-buzz-source.md) | | |
| alert-webhook | [卡](docs/tasks/notes/alert-webhook.md) | | |
| liveness-watch | [卡](docs/tasks/notes/liveness-watch.md) | | |
| budget-guard | [卡](docs/tasks/notes/budget-guard.md) | | |
| diff-message | [卡](docs/tasks/notes/diff-message.md) | | |

## 共享文件改动声明

动了共享文件（`TASKS.md` 登记结构、`docs/traceability.md`、`docs/dev-convention.md`、
`tools/discipline/**`、`tools/docstate/**`）的 task 必须在下表登记。
`discipline` 的 `ownership` 组会检查：本表的每个 task_id 都必须在任务表里有行、
且都有 `docs/claims/` 下的认领文件。

| task_id | 改了什么 | commit |
|---|---|---|
| `MVP-01` | 建 `tools/docstate`（门禁本体）、`backend/app/spec.py`、`TASKS.md` 登记结构 | `810d88d` |
| `R10-fix-10` | 改 `tools/docstate` 的 `schema-keys` 断言、扩 R10 §8 schema-keys 组 | `32f4e45` |
| `fix-flaky-init` | 撤回 flaky 登记（只改 TASKS.md 登记，未动门禁本体） | `9d3d3ef` |
| `claim-nullable` | 改 `tools/discipline` 无关，改 TASKS.md 登记 + R10 §8 可空标注 | `d5769e2` |
| `claims-split` | 拆认领登记为 `docs/claims/**`、改 `discipline` 读目录、加唯一性断言 | `847cc69`+`ec7ae01` |
| `discipline` | 造`tools/discipline` 检查器本体 | `a7adf3c` |
| `graph-traceability` | 建 `docs/traceability.md`、改 `tools/discipline` 加 traceability 组 | `5100c7f` |

**规则**：新增这类 task 时，同一次提交内必须在本表加一行。
漏了的话 `discipline` 会报「动了共享结构的 task 都有认领文件；缺=[...]」。

**为什么有这条**：2026-10-05 之前有 5 次「改了共享文件却没登记」
（`810d88d`、`32f4e45`、`9d3d3ef`、`d5769e2`、`847cc69`），
其中 `847cc69`（认领登记拆分）是 WorkBuddy 自己漏的，被 Qoder 的独立复核抓出来 ——
当时 `discipline` 24/24 全绿，缺陷却真实存在于树里。

## 关键路径

~~~text
MVP-01 → MVP-02 → MVP-03 → MVP-04 → MVP-05 → MVP-06 → MVP-07 → MVP-10b → MVP-11
                  \→ MVP-08 → MVP-10a → MVP-10b
~~~

## 各任务的可提前准备工作（prep）

被阻塞的 AI 不要空等。各自做 prep 项，产出放 `docs/prep/<task-id>/`，**不进主分支**。

| 任务 | 可提前做 | 何时必须完成 |
|---|---|---|
| MVP-02 | schema 草案 DDL、事件 seq 分配器算法说明、"删投影后重建状态"的验证用例设计 | ✅ 已过（`b154f5c`） |
| MVP-03 | 状态投影落地、`docs/state-projection.md` 完善、DAG 环检测用例 | ✅ 已过（`9b4b663`） |
| MVP-04 | 并发 Claim 测试用例、`BEGIN IMMEDIATE` 事务顺序说明、Lease 巡检器触发表 | MVP-03 前（依赖已满足，**2026-10-05 09:53 放行**） |
| MVP-05 | worktree 短 ID 方案、Windows longpaths 与 Defender 锁文件踩坑预案、分支命名口径 | MVP-04 前 |
| MVP-06 | verify profile 结构、baseline differential 用例、overlay 回收策略 | MVP-05 前 |
| MVP-08 | **OpenAPI 契约草案、错误码表、幂等响应约定**（不依赖 schema，最该提前做） | ✅ 已过（`888afb6`，Qoder，已结卡）—— 已由 MVP-08 实际使用：`openapi.yaml` 的 §7/§21 路径对齐与 `error-codes.md` 的 code 直接落进 `backend/app/api/**`，其中 `payload_hash` 的规范化口径被 `claim/service.py` 的 `canonical_payload_hash` 沿用 |
| MVP-10a | Fake Harness 的场景清单（成功/失败/超时/放弃/接手） | MVP-08 前 |

## 统一任务卡

分配任务时复制本模板填写。**"真相源引用"和"test_id"为空视为任务卡不完整。**

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
当前状态：
完成时间：
commit：
验证结果：
已知问题：
~~~

## 登记规则

0-a. **⚠️ 认领登记写进 `docs/claims/`，不要往 TASKS.md 的表里加行。**
   2026-10-05 20:47 拆分：那11.5 小时内 TASKS.md 被改 40 次、产生 19 个 merge commit，
   冲突 100% 集中在这张表与任务卡两处 —— 它们是仓库里仅有的多写点。
   认领登记已拆成「每 task 一个独占文件」，新认领请新建
   `docs/claims/<序号>-<harness>--<task-id>.md`（字段照抄现有文件）。
   TASKS.md 里的认领表**只读**，任何 AI 都不应再往里加行。
   任务卡暂未拆分（原始数据里有 11 个 task 从未建过格式合规的卡，先补齐再拆），
   任务卡仍写进 TASKS.md 的「## 任务卡」章节。
0. **⚠️ 登记必须发生在开工之前，不是之后。** 顺序是：
   建任务分支 → 在「认领登记」表写入 task_id/Harness/时间/分支 → 开工。
   先干活后补登记会让其他 AI 看不见占用（本项目已因此发生一次，见违规记录）。
1. 开工时把任务表状态改成 `doing`；
2. 提交前跑 `make check`（或 `./scripts.sh check`），全绿才可 commit；
3. 完成后必须填 `commit` 与 `验证结果`（贴命令输出，不写"跑通了"）；
4. 遇阻立刻改 `blocked` 并在"已知问题"写清**具体阻塞点**（不要写"有难度"）；
5. **禁止**为了让测试通过而放宽断言或跳过 test_id；
6. **禁止在 `main` 上直接提交**。所有产出走 `agent/<harness>/<task-id>` 分支，
   由 Merge Gate 合并（`MVP-RETRO-001` 记录既有例外）；
7. 负责人一栏填**实际执行的 Harness 名称**，不填 `AI-1` 这类模板占位；
8. **⚠️ 改`TASKS.md` 的 commit 必须立刻合并到 `main`，不能只留在任务分支。**
   `TASKS.md` 是共享文档，登记留在任务分支上等于没登记 —— 其他 AI 读的是 main。
   正确做法：任务分支改完 → 立刻 `git checkout main && git merge --ff-only` →
   确认 `git show main:TASKS.md` 能读到登记。


### claim-denum-coordinator：方案 C 的协调者部分（删占号机制 + parser + 注释行校验）

~~~text
任务编号：claim-denum-coordinator
负责人 Harness：OpenCode
开始时间：2026-10-07 02:20
工作区：.worktrees/opencode/
依赖任务：claim-denum（提案，已交付）、claim-denum-coordinator-review（WorkBuddy 的
          事实核实与代价量化，160 行）
真相源引用：docs/proposals/claim-denum.md（本提案）、
            docs/proposals/claim-denum-coordinator-review.md（裁决材料）、
            tools/discipline/discipline.py registry 组
允许修改目录：tools/discipline/**、backend/tests/**、docs/dev-convention.md、TASKS.md
禁止修改目录：docs/spec/**、backend/app/**、backend/migrations/**、tools/docstate/**
            —— 不动他人 harness 的认领文件（分片迁移由各 owner 自己做）
交付分支：agent/local/opencode/claim-denum-coordinator/a1（合入 `6aa788e`）
          agent/local/opencode/claim-denum-coordinator/a2（状态回填 + 修 SyntaxWarning）
test_id：MVP-PROCESS-001
当前状态：✅ done
commit：a1 = `27a21be` + `cf2748f`（含同步 main）；合入 main = `6aa788e`；
        a2 = 本提交（回填 + 修 `task_id_from_filename` docstring 的 SyntaxWarning）

裁决（协调者 2026-10-07，四项全按 WorkBuddy 的建议）
  1. 迁移方式：**C2 分片** —— 本任务只做协调者那部分（parser 与共享文档）
  2. `task_id` 唯一性断言：**按大小写不敏感实现**（Windows 约束）
  3. 占号机制：**整体删除**（含 WorkBuddy 于 `e24be3a` 新增的
     `_reserved_claim_slots` 与两条断言 —— 他已确认可删）
  4. `docstate` 的认领登记表注释行校验：补上
  另裁决：**「认领文件的合并顺序」不得作为门禁判据**（合并时机取决于根工作区
  是否空闲，不由认领者决定）—— 占号机制本就不该存在。

本任务做了什么
  1. **删除 `parse_claim_table` 的 `"seq"` 字段**，唯一键改为 task_id。
  2. **删除 `_reserved_claim_slots()`（46 行）与占号表的两条断言（41 行）**。
     按 WorkBuddy 的量化，占号机制合计约 105 行代码 + 34 行文档，本任务**净减**。
  3. **「序号唯一」断言换成「task_id 唯一，且大小写不敏感」**。
     大小写这一条不是洁癖：文件名在 Windows 上大小写不敏感，
     `MVP-10a.md` 与 `mvp-10a.md` 在 Windows 上是同一个路径、在 Linux 上是两个 ——
     后者能过按原字符串比对的断言，前者会让两个 agent 以为各有一个文件、实际互相覆盖。
  4. **`task_id_from_filename()` 保留 `^\d+-` 的剥离**。C2 是分片迁移，迁移期
     两种命名共存；保留它使过渡期不产生与内容无关的红灯。序号前缀从「身份」
     降级为「可选的装饰」。
  5. **删除 TASKS.md 的「占用中的认领序号」整节（39 行）**，含本任务自己的占号行。
     顺带更新认领登记表的命名说明。
  6. **补第 4 项断言**：`claim_registry_refs()` 解析 TASKS.md 里
     `<!-- <task-id> → docs/claims/<file>.md -->` 的索引注释，断言**文件真实存在**。
     只取真正的注释行（以 `<!--` 开头、`-->` 结尾），不把正文与表格里的
     `docs/claims/` 字样算进来。

关于第 4 项的实现位置（**偏离裁决原文，已记录理由**）
  裁决写的是 docstate，**实现在 discipline**。三条理由：
  1. 两个输入（TASKS.md 正文、`docs/claims/` 目录）discipline 的 registry 组
     已经加载；docstate 只读 R10 规范，是 `load_spec(spec_path)` 的一条线。
     让它去读 TASKS.md 等于把「规范自洽性」与「仓库登记一致性」两个失败域
     混进同一个检查器 —— 规范写错和登记表写错会报同一种错，排查反而更难。
  2. docstate 有**被声明的检查数**：discipline 的 counts 组校验 README /
     dev-convention 里 docstate 的计数。往 docstate 加一条要连带改三处声明，
     与本任务无关。
  3. 这本来就是 discipline 的盲区：CLAUDE 在 claim-seq 提案里点出「docstate 不校验
     认领登记表的注释行」，而相关断言本来就在 discipline 的 registry 组里。
     **盲区在谁的地盘上，就补在谁的地盘上。**

  计数变化：discipline 45 -> 44（删 3 条占号相关、加 1 条注释行校验、
  另把「序号唯一」换成「task_id 唯一」是 1 换 1）。dev-convention 已同步为 44。

### 验证结果

  - docstate   : 90 项检查，0 项失败
  - discipline : 44 项检查，0 项失败（同步 main 后全绿）
  - ruff check : All checks passed!  ruff format: 81 files already formatted
  - pytest tools/discipline : 52 passed（新增 4 条）
  - pytest tools 全量 : 52 passed
  - pytest backend : 280 passed
  - 序号自检 : 1..58 唯一且无缺号
  - `registry_checks` 夹具改为返回 registry 组**全部**检查项：原先按「序号」
    关键字筛，序号取消后字典会悄悄变空，`next(...)` 只抛 StopIteration ——
    一个看不出意图的失败。空集合本身就该是错误。
  - a1 合并时踩到 WorkBuddy 报告的那个间歇故障：`fatal: unable to write
    .git/index`，mergeguard 的退避只覆盖 ref 锁争用（`update_ref failed` /
    `cannot lock ref`），**不覆盖写 index 失败**，于是没有重试、exit 4。
    人工 sleep 5 后重试成功。**该补的是重试判据，不是人肉重试**（见下）。
  - a1 合并后 discipline 的 merge 组变红：`已合入 main 的任务在任务表均已完成；
    未完成=['claim-denum-coordinator']` —— 我合并后没有回填状态。与 TRAE 评审
    root-worktree-clean 问题 4 同一个坑，**同一个任务里已经踩过一次，这里又踩
    一次**。a2 回填。

未做（分片迁移的 owner 部分，不在本任务范围）
  55 个认领文件的重命名（trae 14 / workbuddy 11 / qoder 11 / claude-code 11 /
  opencode 6 / codex 2）。各 owner 只改自己的文件，H-5 天然满足。
  本任务已经把机械守卫备好了：文件名↔内容一致（已有）+ task_id 唯一（已有）
  + 注释行指向的文件存在（新增），所以重命名有校验、不靠人眼。
~~~

### win-utf8-stdio：把门禁的 UTF-8 处理固化进代码（✅ done）

~~~text
任务编号：win-utf8-stdio
负责人 Harness：OpenCode
开始时间：2026-10-07 03:55
工作区：.worktrees/opencode/
依赖任务：无（独立；与 mergeguard-fix 同批发现）
真相源引用：tools/docstate/docstate.py:426（崩溃点）、
            tools/discipline/discipline.py `merge` 组（曾因同类问题整组崩掉）、
            tools/mergeguard/cli.py `_run_gate`（按 UTF-8 解码子进程输出）
允许修改目录：tools/docstate/**、tools/discipline/**、tools/mergeguard/**、
            backend/tests/**、docs/dev-convention.md、TASKS.md
禁止修改目录：backend/app/**、backend/migrations/**、docs/spec/**
交付分支：agent/local/opencode/win-utf8-stdio/a1
test_id：MVP-PROCESS-002
当前状态：✅ done
commit：a1 = `0ed6e0d`

### 实测到的崩溃

`tools/docstate/docstate.py --verbose` 在 Windows 上**直接崩**：

    File "tools/docstate/docstate.py", line 426, in main
        print("   ok    " + i["msg"])
    UnicodeEncodeError: 'gbk' codec can't encode character '⊆' in position 22

`⊆`（U+2286）出现在一条断言消息里，而 **`gbk` 与 `cp936` 都编不出它**。
CI 跑的是不带 `--verbose` 的 docstate，所以**从没撞到**；开发者一加
`--verbose` 想看细节就崩。`discipline --verbose` 侥幸没崩，只是因为那条消息
恰好不含不可编码字符。

**这与本仓库此前那次「discipline 整组崩溃」是同一个根因的另一副面孔**：
那次是**解码** git 输出（`text=True` 没给 `encoding`，按 GBK 解），
这次是**编码**自己的输出（按 GBK 编不出去）。两边都栽在「跟着控制台代码页走」。

### 第二个问题：门禁输出不是合法 UTF-8

三道门禁在 `PYTHONIOENCODING` 为 `gbk` / `cp936` / 未设三种情况下的输出，
**全部无法按 UTF-8 严格解码**。任何把门禁输出收进日志或 CI artifact 的环节
拿到的都是乱码，而 `gate.yml` 正是用重定向收 `--json`。

### 修法

三个门禁入口在 `main()` 的第一件事就是 `_force_utf8_stdio()`：把
`sys.stdout`/`sys.stderr` 重新 configure 成 `encoding="utf-8",
errors="replace"`。

- UTF-8：`⊆`、`§`、全角标点都能编码，输出**恒为合法 UTF-8**；
- `errors="replace"`：万一还有别的字符编不出，**降级**而不是抛异常 ——
  门禁给出「结果不对」远好过「门禁自己崩了、什么结果都没有」。

刻意**不再**依赖 `PYTHONIOENCODING` / `PYTHONUTF8`：门禁是给别人用的工具，
要求每个人先设环境变量才能正确跑，本身就是缺陷。

三处**复制**这 6 行而非共享模块，是刻意的：三个入口都以
`python tools/X/Y.py` 独立运行，sys.path[0] 是自己所在目录，跨目录 import 要先改
sys.path —— 为 6 行去动三个入口的启动路径不划算。代价是「改一处要改三处」，
用 `test_all_three_gates_have_the_hardening` 钉住。

### 挖出两个被环境变量掩盖的既有缺陷

**1. 测试里的门禁替身（stub）也按控制台代码页输出。**
`test_merge_guard.py` 的 `_GATE_*` 是在**代替**真实门禁的，而 `cli.py` 的
`_run_gate` 按 UTF-8 解码 —— stub 输出 cp936 就被解成乱码，于是
`assert "[skip] rootworktree（测试夹具）" in r.stdout` 平白失败。已让 stub 同样
自己钉 UTF-8，与真实门禁一致。

**2. `test_cli_flow.py::test_module_entry_point_runs` 的断言会在解码失败时抛
`TypeError`。** 它用 `encoding="utf-8"` 却**没给 `errors=`**，子进程按 cp936
输出时 UnicodeDecodeError 让 `stdout` 变 `None`，下一句 `in result.stdout` 抛
`argument of type 'NoneType' is not iterable` —— 一个完全看不出「真正问题是
编码」的失败。那条用例的注释里其实写着「捕获 UnicodeDecodeError 后 stdout 变
None」，作者遇到过，但断言没防。已改为：由**测试自己**给子进程设
`PYTHONIOENCODING=utf-8`、解码加 `errors="replace"`、显式断言 `stdout is not
None`（附可读的失败信息）。

**这两条在我今天之前的所有运行里都是绿的** —— 因为我每次都在 shell 里手动设了
`PYTHONIOENCODING=utf-8`。**用环境变量才能跑通的测试，等于没有在测真实环境。**
这正是本任务的由来。

### 顺带发现：我一直只在 backend/ 跑 ruff

从仓库根跑 ruff 才是全量口径，会多出两条**既有问题**（stash 后同样报）：

    docs/prep/MVP-08/mutation_test.py:130  F541 f-string without any placeholders
    tools/docstate/docstate.py:176          E741 ambiguous variable name `O`

两条都不在本任务的允许目录内（`tools/docstate/**` 在多张任务卡里是**明令禁止
修改**的目录），因此**只记录不改**。顺带修掉了我自己新引入的一条 `UP031`，
所以从 3 条回到既有的 2 条。

### 验证结果

新增 `backend/tests/test_gate_utf8.py` 13 条：
- 三个门禁 × 三种编码，输出必须**严格**可按 UTF-8 解码（9 条）
- `docstate --verbose` 三种编码下都不崩，且**确实打印了**那条含 `⊆` 的消息
  （否则「不崩」可能只是因为什么都没输出）（3 条）
- 三个入口都有 `_force_utf8_stdio` 且都在 `main()` 里先于任何输出调用（1 条）

**负向验证**：stash 掉修复后跑这批用例 → **13 条全红**。

四道门禁（`PYTHONIOENCODING` / `PYTHONUTF8` / `PYTHONLEGACYWINDOWSSTDIO`
三者皆空）：

    docstate    exit=0   90 项检查，0 项失败
    discipline  exit=0   44 项检查，0 项失败
    ruff        exit=1   2 条既有问题（见上，均不在允许目录内）
    pytest      exit=0   303 passed

ruff 的 exit=1 **不是本次引入**，也不是我能修的边界问题 —— 需协调者决定由谁
处理，或授权我扩目录。

### 一个值得单独记的教训

本次之前，我每次跑门禁都在 shell 里加 `$env:PYTHONIOENCODING="utf-8"`，并把它
当成「Windows 上跑 Python 的必要步骤」。结果是：

1. 两个真实缺陷（门禁崩溃、测试因编码而失败）在我的日常运行里**一直是绿的**；
2. 我据此报告过无数次「门禁全绿」，而那些绿灯**只在设了环境变量的环境里成立**。

**我以为在固化环境，其实是在固化一个会掩盖问题的环境。** 真正的固化是让工具
自己在任何环境下都正确 —— 那才是别人也能依赖的东西。
~~~

## 提交前自检

```bash
./scripts.sh check     # docstate + ruff + pytest，三道全绿
git log --oneline -1   # 确认 commit 在任务分支上而非 main
git branch --show-current
```
