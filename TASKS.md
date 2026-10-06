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
| sweeper-family | Qoder | — | ✅ done（只交付设计；承接 MVP-11 报告 §6） | `agent/local/qoder/sweeper-family/a1` | `SCHED-SWEEP-001` |
| root-worktree-clean | OpenCode | merge-interlock | ✅ done（问题 6 按 C 方案落地） | `agent/local/opencode/root-worktree-clean/a1` | `MVP-PROCESS-002` |

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

**本表只读**。每个 task 的认领记录在 `docs/claims/<序号>-<harness>--<task-id>.md`，
各自独占文件、永不冲突。新认领请新建文件，不要往本表加行。

**规则：领取任务前必须在任务清单写入 `task_id`、Harness 名称、时间、分支。**

### 占用中的认领序号

`docs/claims/` 的文件名以序号开头，而 `discipline` 的 registry 组要求序号**严格连续** 1..N
（实现是 `set(实际) == set(1..N)`）。于是出现一个协作陷阱：

> 某人建了 `NN-harness--task.md` 但还没合入 main 时，main 上就缺 `NN`。
> 此时**任何其他人新建认领都会被同一个断言挡住** —— 不管取 `NN`（会被占）
> 还是 `NN+1`（会留下新的洞）。

2026-10-06 09:05 实际卡住了：main 有 1..47，Qoder 在
`agent/local/qoder/MVP-11/a1` 建了 48 但未合入，我连试 50 和 49 都被判失败。

**所以取号之前先查这一节。** 规则：

1. **先查**：本节列出的「已占未合」序号一律跳过；
2. **后取**：用 `main` 上现有最大序号 +1；
3. **立刻登记**：在下一节填一行，并**尽早合入 main**（合入后删除该行）；
4. **认领文件与本行同一次提交** —— 不要先建文件、事后补登记，那样别人查不到你。

格式（表格由 `discipline` 的 registry 组校验，勿改列名）：

| 序号 | Harness | task_id | 分支 | 登记时间 | 认领文件 |
|---|---|---|---|---|---|
| — | （当前无占用） | — | — | — | — |
| 55 | WorkBuddy | `tasks-split` | 2026-10-06 11:05 | `agent/workbuddy/tasks-split/a1` | `docs/claims/55-workbuddy--tasks-split.md` |

**合入后请把该行删掉。** 例：2026-10-06 09:05 这张表里是 Qoder 的 48；
10:30 它合入后，48 换成 Claude 的 MVP-09、49 是 OpenCode、50 是 Claude、
51 是 Qoder，**1..51 重新连续，本行即失效**。

**为什么用表格而不是让人自己查分支**：占号是跨 worktree 的隐式状态—— claimant
在自己的 worktree 里建了文件，main 上看不到。信息必须落在共享文件里才能被别人看到。
别人看到。这与 H-8 是同一个道理：**先看别人，再动共享结构**。

**为什么不让断言放宽**（另一种意见，记录在此供讨论）：把「连续」改成
「不重复 + 不越界」能消除这个阻塞，但会放过「中间漏登记若干个」的情况 ——
那正是这个断言当初要防的。两害相权，宁可让占号可查，也不放宽校验。


| 序号 | task_id | Harness | 认领时间 | 分支 | 状态 |
|---|---|---|---|---|---|
<!-- MVP-01 → docs/claims/01-workbuddy--MVP-01.md -->
<!-- MVP-02 → docs/claims/02-codex--MVP-02.md -->
<!-- MVP-02-amend → docs/claims/03-claude-code--MVP-02-amend.md -->
<!-- worktree-isolation → docs/claims/04-claude-code--worktree-isolation.md -->
<!-- agent-onboarding → docs/claims/05-claude-code--agent-onboarding.md -->
<!-- discipline → docs/claims/06-claude-code--discipline.md -->
<!-- MVP-03 → docs/claims/07-codex--MVP-03.md -->
<!-- fix-wal-lock-retry → docs/claims/09-workbuddy--fix-wal-lock-retry.md -->
<!-- MVP-08-prep → docs/claims/10-qoder--MVP-08-prep.md -->
<!-- R10-fix-10 → docs/claims/11-workbuddy--R10-fix-10.md -->
<!-- graph-traceability → docs/claims/08-claude-code--graph-traceability.md -->
<!-- fix-flaky-init → docs/claims/14-workbuddy--fix-flaky-init.md -->
<!-- MVP-03-amend → docs/claims/12-trae--MVP-03-amend.md -->
<!-- claim-nullable → docs/claims/19-workbuddy--claim-nullable.md -->
<!-- verify-mvp-04 → docs/claims/21-workbuddy--verify-mvp-04.md -->
<!-- R10-fix-10-card → docs/claims/15-trae--R10-fix-10-card.md -->
<!-- MVP-04 → docs/claims/13-qoder--MVP-04.md -->
<!-- fix-flaky-init-card → docs/claims/18-trae--fix-flaky-init-card.md -->
<!-- MVP-08 → docs/claims/17-qoder--MVP-08.md -->
<!-- MVP-04-verify → docs/claims/20-trae--MVP-04-verify.md -->
<!-- claim-nullable-card → docs/claims/22-trae--claim-nullable-card.md -->
<!-- plan-evidence-failclosed → docs/claims/16-opencode--plan-evidence-failclosed.md -->
<!-- claims-split-review → docs/claims/23-qoder--claims-split-review.md -->
<!-- claims-split → docs/claims/24-workbuddy--claims-split.md -->
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
| claim-seq-adopt | [卡](docs/tasks/notes/claim-seq-adopt.md) | | |

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


## 提交前自检

```bash
./scripts.sh check     # docstate + ruff + pytest，三道全绿
git log --oneline -1   # 确认 commit 在任务分支上而非 main
git branch --show-current
```
