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
| `DOC-AUTH-001` | 权限矩阵一致性 | ⚠️ 仅声明 | **设计输入已落**（permission-matrix 卡：`docs/reports/permission-matrix-design-input.md`——R10 矩阵 7×11 逐行落点、Paperclip 21 键机制参照、三缺口〔403 审计事件名不在 88 项白名单→需 R10-fix；OS ACL 与单账户降级收口；help 协作者面〕、认证任务切片 A-D）；**实现仍待认证任务**（token/403/审计/ACL），验收尺=本矩阵逐格 |
| `DOC-ADR-001` | ADR 清单完整 | ⚠️ 仅声明 | 未实现 |
| `DOC-AO-001` | AO 能力对照表 | ⚠️ 仅声明 | 人工产出 `docs/ao-capability-audit.md`，无断言 |

## 阶段 1：调度内核检查

| test_id | 检查什么 | 强制状态 | 由谁检查 |
|---|---|---|---|
| `CORE-RECOVER-001` | 删除投影后可仅凭事件重建任务状态；失败不留孤儿事件 | ✅ 有断言 | `test_database_events.py`（投影重建、回滚无孤儿、并发定序） |
| `CORE-EXPORT-001` | SQLite→JSONL 异步导出：水位、原子替换、失败重试、恢复按 seq 去重、对账（R10 §15 / ADR-055 / FIN-09） | ✅ 有断言 | `test_jsonl_export.py`（顺序导出与水位推进、幂等重跑、崩溃窗口两只〔替换后未推进水位→残尾修剪去重；替换前失败→文件/水位原样零残留〕、重试队列与自动补导、文件与库分歧触发重建、reconcile 检出 lag/篡改/残尾、CLI `--once`/`--check` 退出码与无库 2）；`python -m app.exporter`；ADR-DEV-004 |
| `OPS-ALERT-001` | 出站告警 webhook：升级事件订阅→HMAC 签名投递→重试与停靠（alert-webhook；机制学习自报告 §2.8，原生实现） | ✅ 有断言 | `test_alerts.py`（升级事件集合由冻结迁移表推导不手抄、游标幂等与跨实例续跑、**本地真 HTTP 服务端**断言签名=hmac(body) 与载荷字段、500→跨轮重试至上限停靠、非 loopback http/缺 secret 拒跑、未配置显式 no-op、CLI 端到端含 exit 码）；`python -m app.alerts` |
| `OPS-LIVENESS-001` | 存活观测面 + R10 §13 L706 零 commit 告警 + stuck 指纹计入续租（liveness-watch；机制学习自 Paperclip watchdog，原生实现） | ✅ 有断言 | `test_liveness.py`（政策回退链与含界阈值、续租为钟底的健康读数、发布 commit 清零窗口、死租约 critical、无租约走 attempt 钟、**零 commit 告警恰一次/attempt**（新 attempt 重计）、未配置不入队、**stuck 三向回归**〔续租不升级 / 不续租仍升级 / 无租约仍升级〕、CLI 干净与 critical 两态 exit 码）；`python -m app.liveness` |
| `OPS-BUDGET-001` | 任务级 token 预算 + 调度前预留：claim 授予前 committed=已结算+held 检查（超限拒、新码 claim.token_budget_exhausted）、授予即插 held、submit 结算（未上报=NULL 保守计）、reconcile 巡检收口、阈值事故+告警、split 子预算 ≤ 父剩余（budget-guard；机制学习自 Paperclip budget-reservations/run-cost-accounting，原生实现） | ✅ 有断言 | `test_budget.py`（三桶 committed 与含界分类、预留在场/拒绝零写入、拒绝码、结算恰一次与未上报不落零、reconcile 变异验证、阈值告警幂等与未配置不入队、split 边界、CLI 四态 exit 码）；`python -m app.budget` |
| `MVP-UI-002` | diff 消息进任务线程：`GET /tasks/{id}/diff` 只读端点（numstat 统计 + 有界 patch〔64KiB 上限 + truncated〕+ available:false 不发明数据）+ TaskPane 线程内 diff 卡（文件/计数/展开 patch；budget-guard 之后吸收⑤；机制学习自 Buzz kind:40008，原生重写） | ✅ 有断言 | `test_task_diff.py`（真 git 仓统计与发布前 unavailable、attempt 指定/默认解析、404、patch 字节上限与截断 flag、二进制识别）；`frontend` vitest（TaskPane diff 四态 + api URL 拼接） |
| `MVP-UI-003` | 后端根路径落地页：`GET /` 静态入口页（/health、/docs、/spec/status、前端 5173 提示）+ `GET /favicon.ico` 204；不进 OpenAPI schema；Host 校验对根路径同样生效（landing-page） | ✅ 有断言 | `test_landing.py`（200 text/html 与四链接、204 空体、非 loopback Host→400、schema 不含根路由） + 实机 curl 留档 |
| `MVP-UI-004` | 前端人操作面：管理链（建项目/群组/计划/审批）与 §18 五决策动词（resume/reopen/release/adjudicate/cancel）接入三栏壳；动作按后端守卫状态上下文给出；零后端改动（human-console） | ✅ 有断言 | `frontend` vitest（api 契约 10 方法、三组件动作矩阵、Shell 编排与 notice）+ 浏览器实测留档（管理链真库；awaiting_human 服务层种子后界面恢复） |
| `OPS-DRILL-001` | Codex＋OpenCode 真实联调 + 故障演练 + 前端追溯（e2e-harness-drill） | ⚠️ 仅声明 | `docs/reports/e2e-real-harness-drill.md`：6 条真实全链 merged（codex/opencode；含重启重放补跑）、六项故障演练原始证据（并发恰一胜者/lease_expire 幂等/stale/409 fencing 对照/重放续跑/worktree 隔离）、前端快照追溯；附修复=alternates CRLF（字节断言+变异验证）；F1-F13 移交清单；脚本 gitignored，命令可复跑（非 CI 依赖） |
| `CORE-CLAIM-001` | Claim 幂等：重复 `claim_request_id` 返回首次结果，批量按任务项返回 | ✅ 有断言 | `test_claim.py`（重放返回首次结果且不烧 attempt、按任务项回放走 `task_claims`、payload 冲突拒绝整请求） |
| `CORE-CLAIM-002` | 两个真实 SQLite 连接并发抢同一任务，只有一个成功 | ✅ 有断言 | `test_claim_concurrency.py`（`threading.Barrier` + 两个连接 + 断言 epoch 只 +1、恰一条 attempt/Lease） |
| `CORE-REWORK-001` | 返工链 `claim_for_revision`（R10 §10 L449/L523）：verify 失败→changes_requested→重新领取创建 attempt_no+1/新 epoch/新 Lease（资源与次数预算同首领、budget-guard 同口径、条件 UPDATE 竞态护栏、事件带 revised_from）；resubmit 字面边不落实现（会绕 §16 receipt，裁决留卡）；list_ready_tasks 纳入 changes_requested（rework-loop；F1 修复） | ✅ 有断言 | `test_rework_loop.py`（全环回归=失败→返工→merge、重放幂等、并发恰一、次数预算与 override、资源被占拒绝零写入、budget 预留联动、list_ready_tasks 可见性、变异验证） |
| `CORE-RUNTIME-001` | 运行环 v1：Verify/Merge 出自动驱动（scan=verifying/approved 候选、profile 取自任务快照、已通过 receipt 跳过、并发上限 concurrent_verify、merge 串行、策略文件 AP_RUNTIME_POLICY_FILE、CLI --check/--once/--loop；review 不驱动=外部主体行为） | ✅ 有断言 | `test_runtime_driver.py`（scan 正确性与跳过、真链 verify→awaiting_review、merge→merged、并发上限、幂等双跑、review 边界、CLI 与策略缺失拒绝） |
| `CORE-LEASE-001` | Lease 创建 / 续期 / 过期回收 | ✅ 有断言 | `test_lease.py`（续期续期、过期拒绝、巡检转 `stale` 并释放 Lease 与资源、`reclaim`/`take_over`） |
| `CORE-FENCE-001` | 旧 epoch 的写入被拒 | ✅ 有断言 | `test_lease.py`（`guarded_write` 单一入口；旧 epoch 的 renew / take_over 被拒） |
| `CORE-SPLIT-001` | 父子任务拆分与 `upstream_invalidated_seq` | 🟡 部分有断言 | 拆分闭环已实现并有断言（split-closure，`test_split_closure.py`）：split 产子任务计划（draft plan+snapshot）→ 标准 `plan.approve` 放行；全部子任务 merged → `all_children_merged` 唤醒父任务；`task_dependencies` 方向契约（R10 §10 L587）含回归测试；stuck/blocked_sla 不为难健康 split。**缺口**：`upstream_invalidated_seq`（revert 影响链写侧）仍属 MERGE-REVERT-002 |
| `CORE-STATE-COVERAGE-001` | 所有非终态都有恢复出口的集成级覆盖 | ✅ 有断言（7 态显式豁免+反向守卫） | `test_state_coverage.py`：21 个非终态的完整性、映射边的合法性/触发器存在性/引用测试存在性均为机械断言；14 态映射到活的集成测试（含本任务新增的 blocked、changes_requested 出口补测），5 态无出口机制 + 2 态不可达为显式豁免且带反向守卫（实现即红）。豁免清单与理由见终审报告 §3 |

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
| `MERGE-REVERT-002` | revert 下游反向闭包覆盖全部下游状态 | 🟡 部分有断言 | `test_merge.py`：revert 失败或无法恢复时的出口已覆盖（revert_red → escalated，health 同步）；**缺口**：下游反向闭包（upstream_invalidated_seq、下游 Claim 冻结、merged 下游进 escalated）未实现。**MVP-11 复核**：属特性缺口而非测试缺口，已记录于终审报告 §5/§6-P2-2，随 revert 影响分析特性实现 |

## MVP 专有 ID（清单新增，不在 R10 §26 阶段列表内）

| test_id | 检查什么 | 强制状态 | 由谁检查 |
|---|---|---|---|
| `MVP-API-001` | 仅通过 API 能跑完「建群 → 发方案 → 领取 → 提交 → 审查」 | 🟡 部分有断言 | `test_api_flow.py` 覆盖 HTTP 面上的建群/发方案/批准/领取/心跳/提交/审查；**缺口**：链路上两个迁移不属 MVP-08 —— `claimed → in_progress` 需要 worktree（MVP-05），`verifying → awaiting_review` 需要 verify receipt（MVP-06）。测试里这两步由调度器服务层驱动并在用例注释中标注，**没有伪造 receipt**。消息与事件共享全局 seq 的缺口已由 `message-global-seq`（迁移 003 单行表 `global_seq` 统一发号、task_events 去 AUTOINCREMENT）关闭：`test_api_events_messages.py` 断言两流交错严格递增、跨群不重号，并集为同一连续区间；`test_database_events.py` 覆盖存量库迁移续号与回滚不空洞；消息 refs「接收即丢」的缺口已由 `message-refs-persist`（迁移 004 为 task_messages 增加 JSON TEXT 列 refs，存量行回填 `[]`；post_message 同事务落库、list_messages 解码回数组）关闭：`test_api_events_messages.py` 断言 POST→GET refs 原样读回、缺省为 `[]`、非法 refs 拒绝，`test_database_events.py` 覆盖旧库（001~003）升级回填与升级后新消息往返 |
| `MVP-UI-001` | 人类不直接访问数据库即可完成一条完整任务链路；前端不能绕过 API 改权威字段 | ✅ 有断言 | ①**CLI**：`tests/test_cli_flow.py`（6 项，建群→发方案→看板→领取→提交→审查；「不绕过 API」由**源码级断言**守——CLI 不得 import `app.db`/任何 service）。②**网页控制台**（frontend-thread，2026-10-08）：R10 §22 三栏壳 + 项目群（群聊读/发）+ 任务线程（事件流），`frontend/src/__tests__/**` 21 项（api 客户端 URL/错误信封、三组件状态、路由编排、对比度子进程）+ `vue-tsc` typecheck + `vite build` + `check:contrast`（9 对 token 全 ≥4.5:1）；真实浏览器实测留档（真后端 + vite：落点自动跳群、发消息→API 确证、任务深链 `/g/:gid/t/:tid` 冷启动刷新、console 零错误、网络全 200）。前端只调 API（`src/api.ts` 是唯一数据出入口）。**缺口**：组件测试未入共享门禁（本仓无 git remote、CI 不构成验证面；`scripts.sh check` 不跑 npm；本地需 `npm install`——接入与否需 fleet 决策，见 frontend-thread 卡）；链路上 `claimed → in_progress` 与 verify receipt 两步仍无 HTTP 通路（前者为 §7 无此 verb），见下方 §7 差额一节 |
| `MVP-ADAPTER-001` | Fake Harness 完整跑通 | ✅ 有断言 | `test_adapter.py`（**先从 R10 §7 解析动词表**再断言接口与实现对得上；Fake Harness 走真实 HTTP 跑完 claim→heartbeat→commit→publish→submit→verify→review→merge→merged；失败结果进 changes_requested；abandon；Lease 过期→stale→reclaim→take_over；旧 epoch 被拒；未注册 Harness 的 probe 判 degraded；**十六个 §7 动词全部接上真实路由**——`test_every_section_7_verb_speaks_http` 断言每个动词都直达传输层）。**缺口已清零**：`accept_help`/`reject_help`/`split_task` 由 adapter-verbs 转正（`ask_human` 于 human-ops；`test_adapter_verbs.py` 覆盖三动词矩阵与 HTTP 端到端） |
| `MVP-ADAPTER-002` | 至少一个真实 Harness 完成 Claim / Heartbeat / Submit / Review | ✅ 有断言 | `test_real_adapters.py`（三个真实 Adapter —— codex / claude-code / opencode —— 各自注册可追踪（reg-001→reg-002 后旧版仍可 pinned），argv 契约逐字断言，并走真实 HTTP 完成 claim→heartbeat（CLI 运行期间回执有断言）→commit/publish（Push Proxy）→submit→verify→review→merge→merged；超时→杀进程→abandon；重启丢 token→复放 Claim 轮换出可用新 token→继续同一 attempt 提交；危险操作拒绝（main 分支、不可达 commit、非 needs_help 态的 help 表决）；`SubprocessRunner` 对真实子进程的 kill-on-deadline 单独验）。**边界**：模型进程多数用例用 ScriptedRunner 替身注入（argv 契约与 SubprocessRunner 各有独立断言补偿）；codex 本机未安装，其 argv 契约来自官方文档（模块 docstring 注明）。**顺带关闭**：10a 遗留的两个「方法存在但路由 404」的 §7 动词 —— `list_ready_tasks`（`redacted` 无 §17 定义，显式 422 拒绝）与 `read_context` 已补路由并逐 Adapter 断言 |
| `MVP-PROCESS-001` | 协作纪律：登记表自洽、任务卡字段完整、依赖图自洽 | ✅ 有断言 | `tools/discipline`（registry / cards / deps / counts 组） |
| `MVP-PROCESS-002` | 合并纪律：已合入 main 的任务必须已登记、已完成且卡证据完整；认领序号唯一 | ✅ 有断言 | `tools/discipline` merge 组 6 项 + registry 序号唯一断言（GATE-MERGE-CHECK-01，含 `test_discipline.py` 与 gate.yml `fetch-depth: 0`；2026-10-05 合 main 时移植到 docs/claims/ 认领目录版检查器）。**序号连续性断言已由 `claim-seq-adopt` 撤销**：它在并发分支下结构上不可满足，替换为「文件名 task_id 与内容一致」，理由与 34 次重命名的机械证据见 `docs/proposals/claim-seq-contiguity.md` |
| `MVP-TRANSITION-MIRROR-001` | 代码迁移表与 R10 §10 逐三元组零差异（声明层） | ✅ 有断言 | `test_transition_mirror.py`（展开三元组双向零差异、展开计数 = 138、R10 §10 的状态与事件全部在代码注册、表结构诊断为空） |
| `MVP-MERGE-LOCK-001` | 共享 main 的合并互斥：锁落在 git common dir、跨 worktree 生效、并发有且只有一个赢家、stale 可接管、脏工作区拒绝、门禁红不提交 merge commit | ✅ 有断言 | `test_merge_guard.py`（真 `git init` 仓库 + 真并发线程 + CLI 子进程端到端，不用 mock）+ `tools/mergeguard/cli.py` |
| `HUMAN-OPS-001` | §18 人类决策面：六操作矩阵、决策台账、租约暂停/重发、提醒退避 | ✅ 有断言 | `test_human_ops.py`（六操作状态矩阵与拒绝、租约释放/同 attempt·epoch 重签、决策行生命周期、提醒退避 30→60→120→240 封顶且不动 waiting/state seq、HTTP 端到端含 §13 fencing 拒绝、CLI 端到端）；`/human/*` + `/adapter/ask_human` 路由；`sweep_human_wait_timeout` / `sweep_escalation_reminder` |
| `MVP-ARCH-001` | Paperclip/Buzz 借鉴边界：R10 为唯一业务真相源，两域借鉴点对照 + 事件名映射 | ⚠️ 仅声明 | `docs/adr/ADR-DEV-003-paperclip-buzz-adoption.md`（**决策文件即交付物**；内容不作机械断言——按本表图例属"仅声明"，约束力由后续借鉴类任务在 scope 声明中引用它承担）。**源码核实修订（2026-10-08，paperclip-buzz-source）**：`docs/reports/paperclip-buzz-source-review.md`（两仓 commit-定版快照逐文件证据；11 事件名三方对照；勘误 addendum 已写入 ADR 第三节） |
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

## R10 §7 Adapter 接口的落地差额（MVP-10a 与 MVP-09 两次独立核实）

R10 §7 规定 v1 主动拉取接口共 **16 个方法**。逐条对照 `backend/app/api/routes.py`：

| 状态 | 方法 |
|---|---|
| 有端点 | **16 个全部有端点**：`claim`、`heartbeat`、`submit_result`、`request_help`、`report_blocked`、`accept_help`、`reject_help`、`split_task`、`ask_human`、`abandon`、`release`、`publish_task_branch`、`probe`、`capabilities`、`list_ready_tasks`、`read_context` |
| **无端点** | 无（差额清零） |

转正时间线：`list_ready_tasks` / `read_context`（MVP-10b）；`ask_human`（human-ops，
2026-10-07，带 attempt/epoch 走 §13 守卫）；`accept_help` / `reject_help` /
`split_task`（adapter-verbs，2026-10-07——help 表决校验 `help_id`（`request_help`
mint、随事件负载落库），split 走 §13 fence + `command_idempotency` 幂等，
`UnsupportedVerb` 类已删除；`test_adapter_verbs.py` 覆盖）。

### 两个不在 §7 里、但操作链路离不开的步骤

这两个是 **MVP-09（CLI）核实出来的**，因为它是第一个真把整条链路走完的交付：

| 步骤 | 为何没有 HTTP 通路 |
|---|---|
| `claimed → in_progress`（`start_task`） | **§7 根本没有这个 verb**。§7 只有推送模型的 `start(task, workspace)`，且被标为「未来才需要」。而 `submit_result` 要求任务已在 `in_progress` |
| 产出 verify receipt（`verifying → awaiting_review`） | Verify Worker 没有端点。R10 §7 也不含 verify 动词 —— 它属于调度中心/Worker 内部动作 |

**后果**：操作员（或任何 harness）**无法只经 API 走完「领取 → 提交 → 审查」**。
`tests/test_cli_flow.py` 与 `tests/test_api_flow.py` 都是**直接调
`SchedulerService.start_task` / `VerifyWorker.run`** 绕过，并在用例注释里显式标注
—— 而不是伪造 receipt 或偷偷开一条旁路。

**要真正闭合**，需要在 §7 增补 verb（并走 R10 复审），或明确「这两步不属 adapter
职责、由调度中心内部驱动」—— 后者目前是对的，但**没有任何一处规范文字这么说过**，
所以每个实现者都要自己重新发现一遍。本表把这条写下来就是为了省掉那一次。

> 上表**不被门禁断言**（`discipline` 只查 test_id 是否登记）。加断言前先核对此处；
> 发现差额未同步时，改的是这张表而不是实现。

## 相关

- 检查 ID 的唯一真相源是 `docs/spec/0.3-R10.md` §26（阶段落点）与 §37.7（事件 allowlist）
- 本文件不得新增 R10 未登记的 ID 形态；新增须先改 R10 正文并升 `schema_version`
- 哪些约束真的会被门禁红掉，见 `docs/dev-convention.md` 的「约束的强制程度」