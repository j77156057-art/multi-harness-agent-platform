# 测试追溯矩阵

R10 §26 的检查 ID 体系在本项目的登记处。`dev-convention.md` H-4 要求
「完成标准必须引用检查 ID 并登记到本文件」——本文件此前**不存在**，那条硬约束
指向了一个空的地方。现由 `tools/discipline` 的 `traceability` 组断言：任务表里
出现的每个 `test_id` 都必须在此登记。

## 强制状态列的含义

| 标记 | 含义 |
|---|---|
| ✅ 有断言 | 有脚本/测试会因它失败而变红 |
| 🟡 部分有断言 | 有断言，但该 ID 声称的检查里有一部分**明确未被覆盖**，缺口写在「由谁检查」列。**列为 🟡 的都不是「基本完成」，而是「缺口已知且写下来了」** |
| ⚠️ 仅声明 | ID 已登记，但没有任何东西检查它是否达成 |
| ❌ 未实现 | 对应 MVP 任务尚未开工 |

**这一列本身不被断言**（除了本文件是否存在、以及任务表的 test_id 是否在此登记）。
判断某个 ID 是否真的覆盖了它声称的行为，仍需人读 —— 这是
`dev-convention.md`「已知盲区」里明写的一条。

## 阶段 0：文档级检查

| test_id | 检查什么 | 强制状态 | 由谁检查 |
|---|---|---|---|
| `DOC-STATE-001` | 状态机自洽：allowlist 封闭性、迁移表单值 next、自环清单、非终态出边 | ✅ 有断言 | `tools/docstate` structure/allowlist/transitions/r10-events/loops/states 组 |
| `DOC-SCHEMA-001` | canonical schema 自洽：§8 唯一键引用的列已声明、§11 DDL 列已登记、§37.6 必要字段 | ✅ 有断言 | `tools/docstate` schema/schema-keys/schema-list 组 |
| `DOC-TRACE-001` | test_id 无悬空：全文出现的 ID 必须落在阶段列表之一 | ✅ 有断言 | `tools/docstate` trace 组 |
| `DOC-PLAN-001` | 计划 linter：拒绝环 / 非法引用 / 超规模 / 不可满足 evidence | ✅ 有断言 | `test_state_plan.py`（拒绝环、未注册 capability、`limits` 超规模、重复依赖边、glob 重复、`cmd` 判据缺 evidence source、未注册 evidence source、空白 evidence source；`human` 判据免 evidence source）。原实现 fail-open（`if source is not None` + `elif evidence_sources and ...`），由 `plan-evidence-failclosed` 改为 fail-closed || `DOC-AUTH-001` | 权限矩阵一致性 | ⚠️ 仅声明 | 未实现（MVP-05 交付） |
| `DOC-ADR-001` | ADR 清单完整 | ⚠️ 仅声明 | 未实现 |
| `DOC-AO-001` | AO 能力对照表 | ⚠️ 仅声明 | 人工产出 `docs/ao-capability-audit.md`，无断言 |

## 阶段 1：调度内核检查

| test_id | 检查什么 | 强制状态 | 由谁检查 |
|---|---|---|---|
| `CORE-RECOVER-001` | 删除投影后可仅凭事件重建任务状态；失败不留孤儿事件 | ✅ 有断言 | `test_database_events.py`（投影重建、回滚无孤儿、并发定序） |
| `CORE-CLAIM-001` | Claim 幂等：重复 `claim_request_id` 返回首次结果，批量按任务项返回 | ✅ 有断言 | `test_claim.py`（重放返回首次结果且不烧 attempt、按任务项回放走 `task_claims`、payload 冲突拒绝整请求） |
| `CORE-CLAIM-002` | 两个真实 SQLite 连接并发抢同一任务，只有一个成功 | ✅ 有断言 | `test_claim_concurrency.py`（`threading.Barrier` + 两个连接 + 断言 epoch 只 +1、恰一条 attempt/Lease） |
| `CORE-LEASE-001` | Lease 创建 / 续期 / 过期回收 | ✅ 有断言 | `test_lease.py`（续期续期、过期拒绝、巡检转 `stale` 并释放 Lease 与资源、`reclaim`/`take_over`） |
| `CORE-FENCE-001` | 旧 epoch 的写入被拒 | ✅ 有断言 | `test_lease.py`（`guarded_write` 单一入口；旧 epoch 的 renew / take_over 被拒） |
| `CORE-SPLIT-001` | 父子任务拆分与 `upstream_invalidated_seq` | ⚠️ 仅声明 | 未实现（MVP-03/04） |
| `CORE-STATE-COVERAGE-001` | **行为层**：每个 owner 非终态的恢复出口在 DB 投影上真能跑通，且巡检器按阈值触发 | ⚠️ 仅声明 | 未实现（MVP-11）。**与 `MVP-TRANSITION-MIRROR-001` 的分工**：后者只保证「表里有这条边、且边的 current/event/next 与 R10 一致」（**声明层**）；本 ID 保证「这条边被巡检器真的执行」（**行为层**）——`stuck_threshold_reached` / `stuck_human_threshold` 阈值、`blocked｜unblock`、`ready_waiting_deps｜orphan_detected`、`awaiting_human` 进入时暂停 Lease 与 resume 时重签、`stale → reclaiming → claimed` 全链路。声明层已由 MVP-03-amend 覆盖，行为层仍是 MVP-11 的主要工作量 |

## 阶段 2：Git / Verify / Merge 检查

| test_id | 检查什么 | 强制状态 | 由谁检查 |
|---|---|---|---|
| `GIT-ACL-001` | 两个任务不共享可写 worktree | ⚠️ 仅声明 | 未实现（MVP-05） |
| `GIT-ACL-002` | 直接写集成分支的测试失败；旧 epoch 发布失败 | ⚠️ 仅声明 | 未实现（MVP-05） |
| `GIT-PACK-001` | Defender 开启时跨账户 pack/bundle 传输与 intent 对账 | ⚠️ 仅声明 | 未实现（MVP-05） |
| `VERIFY-DIFF-001` | baseline differential 语义 | ⚠️ 仅声明 | 未实现（MVP-06） |
| `VERIFY-RECEIPT-001` | receipt 与待合并 commit 不一致时 Merge Gate 拒绝 | ⚠️ 仅声明 | 未实现（MVP-06） |
| `MERGE-EXACT-001` | receipt / review / 分支 head 三方 commit 交叉断言 | ⚠️ 仅声明 | 未实现（MVP-07） |
| `MERGE-REVERT-001` | revert 树断言 | ⚠️ 仅声明 | 未实现（MVP-07） |
| `MERGE-REVERT-002` | revert 下游反向闭包覆盖全部下游状态 | ⚠️ 仅声明 | 未实现（MVP-07） |

## MVP 专有 ID（清单新增，不在 R10 §26 阶段列表内）

| test_id | 检查什么 | 强制状态 | 由谁检查 |
|---|---|---|---|
| `MVP-API-001` | 仅通过 API 能跑完「建群 → 发方案 → 领取 → 提交 → 审查」 | 🟡 部分有断言 | `test_api_flow.py` 覆盖 HTTP 面上的建群/发方案/批准/领取/心跳/提交/审查；**缺口**：链路上两个迁移不属 MVP-08 —— `claimed → in_progress` 需要 worktree（MVP-05），`verifying → awaiting_review` 需要 verify receipt（MVP-06）。测试里这两步由调度器服务层驱动并在用例注释中标注，**没有伪造 receipt** |
| `MVP-UI-001` | 人类不直接访问数据库即可完成一条完整任务链路 | ⚠️ 仅声明 | 未实现（MVP-09） |
| `MVP-ADAPTER-001` | Fake Harness 完整跑通 | ⚠️ 仅声明 | 未实现（MVP-10a） |
| `MVP-ADAPTER-002` | 至少一个真实 Harness 完成 Claim / Heartbeat / Submit / Review | ⚠️ 仅声明 | 未实现（MVP-10b） |
| `MVP-PROCESS-001` | 协作纪律：登记表自洽、任务卡字段完整、依赖图自洽 | ✅ 有断言 | `tools/discipline`（registry / cards / deps / counts / traceability 组） |
| `MVP-MERGE-LOCK-001` | 共享 main 的合并互斥：锁落在 git common dir、跨 worktree 生效、并发有且只有一个赢家、stale 可接管、脏工作区拒绝、门禁红不提交 merge commit | ✅ 有断言 | `test_merge_guard.py`（真 `git init` 仓库 + 真并发线程，不用 mock）+ `tools/mergeguard/cli.py` |
| `MVP-TRANSITION-MIRROR-001` | 代码迁移表与 R10 §10 逐三元组零差异（声明层） | ✅ 有断言 | `test_transition_mirror.py`（4 项：展开三元组双向零差异、展开计数 = 138、R10 §10 的状态与事件全部在代码注册、表结构诊断为空） |
| `MVP-RETRO-001` | 记录本项目已发生的流程偏差 | ⚠️ 仅声明 | 人工，见 `TASKS.md` 事故记录 |

## 现状小结

**29 个已登记的 ID里，11 个有断言，18 个仅是名字。**

| 分组 | ID 数 | 有断言 |
|---|---|---|
| 阶段 0 文档级 | 7 | 4 |
| 阶段 1 调度内核 | 7 | 5 |
| 阶段 2 Git/Verify/Merge | 8 | 0 |
| MVP 专有 | 7 | 2 |

阶段 2 全部未实现 —— MVP-05/06/07 一条线都还没开工，这是当前最大的空洞，
也是 R10 §21 那一整套 Merge Gate 语义（commit 三方交叉断言、revert 树断言、
下游反向闭包）目前**完全靠人读**的原因。

这里同时修正过一处过期记录，过程值得留着当教训：`DOC-PLAN-001` 原标注
「未实现（MVP-03 交付）」，而 MVP-03 早已交付 —— 登记表与实现之间没有强制同步。
`trace-closure` 把它改成「⚠️ 3/4 有断言」（记录当时 fail-open 的真实状态），
随后 `plan-evidence-failclosed` 把 fail-open 修掉并补上用例，它才变成「✅ 有断言」。
**同一行改了两次，两次都是对的**：中间态如实反映了当时的代码，不是错误记录。

由此得出一条结构性事实：**登记表本身也会过期**。`traceability` 组只检查
「任务表里的 test_id 是否在此登记」，不检查「本文件声称的强制状态是否仍然成立」。
任何人修完实现之后，都必须记得回来改这一行——否则下一个读它的人会被误导，
而这正是本文件第 16 行所说的盲区的延伸。

这也是把登记处补齐的价值：现在能一眼看出哪些是名字、哪些是真门禁。

## 相关

- 检查 ID 的唯一真相源是 `docs/spec/0.3-R10.md` §26（阶段落点）与 §37.7（事件 allowlist）
- 本文件不得新增 R10 未登记的 ID 形态；新增须先改 R10 正文并升 `schema_version`
- 哪些约束真的会被门禁红掉，见 `docs/dev-convention.md` 的「约束的强制程度」