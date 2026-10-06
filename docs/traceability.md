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
| `DOC-PLAN-001` | 计划 linter：拒绝环 / 非法引用 / 超规模 / 不可满足 evidence | ✅ 有断言 | `test_state_plan.py`（拒绝环、未注册 capability、`limits` 超规模、重复依赖边、glob 重复、`cmd` 判据缺 evidence source、未注册 evidence source、空白 evidence source；`human` 判据免 evidence source）。原实现 fail-open（`if source is not None` + `elif evidence_sources and ...`），由 `plan-evidence-failclosed` 改为 fail-closed |
| `DOC-AUTH-001` | 权限矩阵一致性 | ⚠️ 仅声明 | 未实现（MVP-05 交付） |
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
| `CORE-STATE-COVERAGE-001` | 所有非终态都有恢复出口的集成级覆盖 | ⚠️ 仅声明 | 未实现（MVP-11） |

## 阶段 2：Git / Verify / Merge 检查

| test_id | 检查什么 | 强制状态 | 由谁检查 |
|---|---|---|---|
| `GIT-ACL-001` | 两个任务不共享可写 worktree | ✅ 有断言 | `test_git_worktree.py`（活动 workspace 路径互斥断言、同 attempt 重复 provision 幂等、短 ID 目录、归档校验通过才回收） |
| `GIT-ACL-002` | 直接写集成分支的测试失败；旧 epoch 发布失败 | ✅ 有断言 | `test_publish.py`（真跑 `git push` 被 canonical 钩子拒绝；旧 epoch 发布被拒且不写 intent；alternates 越界被拒） |
| `GIT-PACK-001` | Defender 开启时跨账户 pack/bundle 传输与 intent 对账 | 🟡 部分有断言 | 传输与 intent 对账已覆盖（`test_publish.py` 断言对象进入 canonical、`side_effect_intents(type=push)` 落库并可重放）；**缺口**：单账户下不存在跨账户传输，`Defender` 开启场景未覆盖，见 `docs/adr/ADR-DEV-002-single-account-acl.md` |
| `VERIFY-DIFF-001` | baseline differential 语义 | ✅ 有断言 | `test_verify.py`（head 通过才进 awaiting_review；head 失败进 changes_requested 且无 verify_passed 事件；在精确 commit 的全新 detached worktree 上跑；new_test_files 取自 diff）。**MVP 缺口**：`base 必须失败` 一侧只记录不强制，flaky 放行为阶段 2，见 MVP-06 卡 |
| `VERIFY-RECEIPT-001` | receipt 与待合并 commit 不一致时 Merge Gate 拒绝 | ✅ 有断言 | `test_verify.py` + `app/verify/receipts.py`（`merge_is_verifiable` 按精确 commit 查 passing receipt，base commit 查不到即拒绝；重复 verify 返回首次 receipt；不同 commit 生成独立 receipt；§16 字段齐全）。Merge Gate 本体在 MVP-07 接入 |
| `MERGE-EXACT-001` | receipt / review / 分支 head 三方 commit 交叉断言 | ✅ 有断言 | `test_merge.py` + `app/merge/gate.py`（三条交叉断言各有拒绝用例；提交前不写集成分支，拒绝后断言 ref 未移动；无 receipt / 无 review / 别人的 commit 都进不来） |
| `MERGE-REVERT-001` | revert 树断言 | ✅ 有断言 | `test_merge.py`（断言 tree(revert) == tree(first_parent(merge))、revert 是失败合并的直接子提交、merge..revert 只含 1 个提交；集成分支指向 revert） |
| `MERGE-REVERT-002` | revert 下游反向闭包覆盖全部下游状态 | 🟡 部分有断言 | `test_merge.py`：revert 失败或无法恢复时的出口已覆盖（revert_red → escalated，health 同步）；**缺口**：下游反向闭包（upstream_invalidated_seq、下游 Claim 冻结、merged 下游进 escalated）未实现，属 MVP-11 的集成级覆盖 |

## MVP 专有 ID（清单新增，不在 R10 §26 阶段列表内）

| test_id | 检查什么 | 强制状态 | 由谁检查 |
|---|---|---|---|
| `MVP-API-001` | 仅通过 API 能跑完「建群 → 发方案 → 领取 → 提交 → 审查」 | 🟡 部分有断言 | `test_api_flow.py` 覆盖 HTTP 面上的建群/发方案/批准/领取/心跳/提交/审查；**缺口**：链路上两个迁移不属 MVP-08 —— `claimed → in_progress` 需要 worktree（MVP-05），`verifying → awaiting_review` 需要 verify receipt（MVP-06）。测试里这两步由调度器服务层驱动并在用例注释中标注，**没有伪造 receipt**。消息与事件共享全局 seq 的缺口已由 `message-global-seq`（迁移 003 单行表 `global_seq` 统一发号、task_events 去 AUTOINCREMENT）关闭：`test_api_events_messages.py` 断言两流交错严格递增、跨群不重号，并集为同一连续区间；`test_database_events.py` 覆盖存量库迁移续号与回滚不空洞；消息 refs「接收即丢」的缺口已由 `message-refs-persist`（迁移 004 为 task_messages 增加 JSON TEXT 列 refs，存量行回填 `[]`；post_message 同事务落库、list_messages 解码回数组）关闭：`test_api_events_messages.py` 断言 POST→GET refs 原样读回、缺省为 `[]`、非法 refs 拒绝，`test_database_events.py` 覆盖旧库（001~003）升级回填与升级后新消息往返 |
| `MVP-UI-001` | 人类不直接访问数据库即可完成一条完整任务链路 | ⚠️ 仅声明 | 未实现（MVP-09） |
| `MVP-ADAPTER-001` | Fake Harness 完整跑通 | ✅ 有断言 | `test_adapter.py`（**先从 R10 §7 解析动词表**再断言接口与实现对得上；Fake Harness 走真实 HTTP 跑完 claim→heartbeat→commit→publish→submit→verify→review→merge→merged；失败结果进 changes_requested；abandon；Lease 过期→stale→reclaim→take_over；旧 epoch 被拒；未注册 Harness 的 probe 判 degraded）。**缺口**：`accept_help`/`reject_help`/`split_task`/`ask_human` 四个 §7 动词尚无路由，方法存在但抛 `UnsupportedVerb` |
| `MVP-ADAPTER-002` | 至少一个真实 Harness 完成 Claim / Heartbeat / Submit / Review | ✅ 有断言 | `test_real_adapters.py`（三个真实 Adapter —— codex / claude-code / opencode —— 各自注册可追踪（reg-001→reg-002 后旧版仍可 pinned），argv 契约逐字断言，并走真实 HTTP 完成 claim→heartbeat（CLI 运行期间回执有断言）→commit/publish（Push Proxy）→submit→verify→review→merge→merged；超时→杀进程→abandon；重启丢 token→复放 Claim 轮换出可用新 token→继续同一 attempt 提交；危险操作拒绝（main 分支、不可达 commit、旧 epoch、四个无路由动词）；`SubprocessRunner` 对真实子进程的 kill-on-deadline 单独验）。**边界**：模型进程多数用例用 ScriptedRunner 替身注入（argv 契约与 SubprocessRunner 各有独立断言补偿）；codex 本机未安装，其 argv 契约来自官方文档（模块 docstring 注明）。**顺带关闭**：10a 遗留的两个「方法存在但路由 404」的 §7 动词 —— `list_ready_tasks`（`redacted` 无 §17 定义，显式 422 拒绝）与 `read_context` 已补路由并逐 Adapter 断言 |
| `MVP-PROCESS-001` | 协作纪律：登记表自洽、任务卡字段完整、依赖图自洽 | ✅ 有断言 | `tools/discipline`（registry / cards / deps / counts 组） |
| `MVP-PROCESS-002` | 合并纪律：已合入 main 的任务必须已登记、已完成且卡证据完整；认领序号唯一连续 | ✅ 有断言 | `tools/discipline` merge 组 6 项 + registry 序号连续断言（GATE-MERGE-CHECK-01，含 `test_discipline.py` 14 项与 gate.yml `fetch-depth: 0`；2026-10-05 合 main 时移植到 docs/claims/ 认领目录版检查器，共 39 项） |
| `MVP-TRANSITION-MIRROR-001` | 代码迁移表与 R10 §10 逐三元组零差异（声明层） | ✅ 有断言 | `test_transition_mirror.py`（展开三元组双向零差异、展开计数 = 138、R10 §10 的状态与事件全部在代码注册、表结构诊断为空） |
| `MVP-MERGE-LOCK-001` | 共享 main 的合并互斥：锁落在 git common dir、跨 worktree 生效、并发有且只有一个赢家、stale 可接管、脏工作区拒绝、门禁红不提交 merge commit | ✅ 有断言 | `test_merge_guard.py`（真 `git init` 仓库 + 真并发线程 + CLI 子进程端到端，不用 mock）+ `tools/mergeguard/cli.py` |
| `MVP-RETRO-001` | 记录本项目已发生的流程偏差 | ⚠️ 仅声明 | 人工，见 `TASKS.md` 事故记录 |

## 现状小结

**31 个已登记的 ID 里，15 个有断言，2 个部分有断言，14 个仅是名字。**

| 分组 | ID 数 | 有断言 |
|---|---|---|
| 阶段 0 文档级 | 7 | 4 |
| 阶段 1 调度内核 | 7 | 5 |
| 阶段 2 Git/Verify/Merge | 8 | 2 |
| MVP 专有 | 9 | 4 |
`DOC-TRACE-001`、`CORE-RECOVER-001`，外加 `MVP-PROCESS-001`）。

阶段 1 与阶段 2 的 ID 几乎全部未实现 —— 这与「MVP 才走到 MVP-02」一致，
不是缺陷，但意味着**关键路径上大部分「完成标准」目前是不可验收的**：
它们只是名字。R10 §29 的各阶段退出条件大量依赖这些 ID，在对应任务交付前
无法机械判定。

这也是把登记处补齐的价值：现在能一眼看出哪些是名字、哪些是真门禁。

## 相关

- 检查 ID 的唯一真相源是 `docs/spec/0.3-R10.md` §26（阶段落点）与 §37.7（事件 allowlist）
- 本文件不得新增 R10 未登记的 ID 形态；新增须先改 R10 正文并升 `schema_version`
- 哪些约束真的会被门禁红掉，见 `docs/dev-convention.md` 的「约束的强制程度」