# 测试追溯矩阵

R10 §26 的检查 ID 体系在本项目的登记处。`dev-convention.md` H-4 要求
「完成标准必须引用检查 ID 并登记到本文件」——本文件此前**不存在**，那条硬约束
指向了一个空的地方。现由 `tools/discipline` 的 `traceability` 组断言：任务表里
出现的每个 `test_id` 都必须在此登记。

## 强制状态列的含义

| 标记 | 含义 |
|---|---|
| ✅ 有断言 | 有脚本/测试会因它失败而变红 |
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
| `DOC-PLAN-001` | 计划 linter：拒绝环 / 非法引用 / 超规模 / 不可满足 evidence | ⚠️ 仅声明 | 未实现（MVP-03 交付） |
| `DOC-AUTH-001` | 权限矩阵一致性 | ⚠️ 仅声明 | 未实现（MVP-05 交付） |
| `DOC-ADR-001` | ADR 清单完整 | ⚠️ 仅声明 | 未实现 |
| `DOC-AO-001` | AO 能力对照表 | ⚠️ 仅声明 | 人工产出 `docs/ao-capability-audit.md`，无断言 |

## 阶段 1：调度内核检查

| test_id | 检查什么 | 强制状态 | 由谁检查 |
|---|---|---|---|
| `CORE-RECOVER-001` | 删除投影后可仅凭事件重建任务状态；失败不留孤儿事件 | ✅ 有断言 | `test_database_events.py`（投影重建、回滚无孤儿、并发定序） |
| `CORE-CLAIM-001` | Claim 幂等：重复 `claim_request_id` 返回首次结果，批量按任务项返回 | ⚠️ 仅声明 | 未实现（MVP-04） |
| `CORE-CLAIM-002` | 两个真实 SQLite 连接并发抢同一任务，只有一个成功 | ⚠️ 仅声明 | 未实现（MVP-04） |
| `CORE-LEASE-001` | Lease 创建 / 续期 / 过期回收 | ⚠️ 仅声明 | 未实现（MVP-04） |
| `CORE-FENCE-001` | 旧 epoch 的写入被拒 | ⚠️ 仅声明 | 未实现（MVP-04） |
| `CORE-SPLIT-001` | 父子任务拆分与 `upstream_invalidated_seq` | ⚠️ 仅声明 | 未实现（MVP-03/04） |
| `CORE-STATE-COVERAGE-001` | 所有非终态都有恢复出口的集成级覆盖 | ⚠️ 仅声明 | 未实现（MVP-11） |

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
| `MVP-API-001` | 仅通过 API 能跑完「建群 → 发方案 → 领取 → 提交 → 审查」 | ⚠️ 仅声明 | 未实现（MVP-08） |
| `MVP-UI-001` | 人类不直接访问数据库即可完成一条完整任务链路 | ⚠️ 仅声明 | 未实现（MVP-09） |
| `MVP-ADAPTER-001` | Fake Harness 完整跑通 | ⚠️ 仅声明 | 未实现（MVP-10a） |
| `MVP-ADAPTER-002` | 至少一个真实 Harness 完成 Claim / Heartbeat / Submit / Review | ⚠️ 仅声明 | 未实现（MVP-10b） |
| `MVP-PROCESS-001` | 协作纪律：登记表自洽、任务卡字段完整、依赖图自洽 | ✅ 有断言 | `tools/discipline`（registry / cards / deps / counts 组） |
| `MVP-RETRO-001` | 记录本项目已发生的流程偏差 | ⚠️ 仅声明 | 人工，见 `TASKS.md` 事故记录 |

## 现状小结

**20 个已登记的 ID 里，只有 4 个目前有断言**（`DOC-STATE-001`、`DOC-SCHEMA-001`、
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