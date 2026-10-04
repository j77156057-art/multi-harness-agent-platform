# 任务登记表

真相源：`docs/spec/mvp-task-checklist.md`（0.4 重排版）
硬约束 H-4：**没有 `test_id` 的完成标准不可验收。**

## 状态图例

`ready` 现在可开工 / `waiting` 等硬依赖 / `prep` 可做准备工作 / `doing` 进行中 /
`review` 待审/ `done` 已完成 / `blocked` 阻塞

## 任务表

| task_id | 负责人 | 依赖 | 状态 | test_id | 允许修改目录 |
|---|---|---|---|---|---|
| MVP-01 | AI-1 | — | ✅ done | `DOC-STATE-001` `DOC-TRACE-001` | `backend/**` `docs/**` `tools/**` `frontend/**` `.github/**` |
| MVP-02 | AI-2 | MVP-01 | 🔜 ready | `DOC-SCHEMA-001` `CORE-RECOVER-001` | `backend/app/db/**` `backend/app/events/**` `backend/migrations/**` |
| MVP-03 | AI-3 | MVP-02 | ⏳ waiting | `DOC-STATE-001` `DOC-PLAN-001` | `backend/app/state/**` `backend/app/plan/**` |
| MVP-04 | AI-4 | MVP-02 MVP-03 | ⏳ waiting ★瓶颈 | `CORE-CLAIM-001` `CORE-CLAIM-002` `CORE-LEASE-001` `CORE-FENCE-001` | `backend/app/claim/**` `backend/app/lease/**` |
| MVP-05 | AI-5 | MVP-03 MVP-04 | ⏳ waiting | `GIT-ACL-001` `GIT-ACL-002` | `backend/app/git/**` `backend/app/worktree/**` |
| MVP-06 | AI-6 | MVP-05 | ⏳ waiting | `VERIFY-DIFF-001` `VERIFY-RECEIPT-001` | `backend/app/verify/**` `backend/app/evidence/**` |
| MVP-07 | AI-7 | MVP-06 | ⏳ waiting | `MERGE-EXACT-001` `MERGE-REVERT-001` `MERGE-REVERT-002` | `backend/app/review/**` `backend/app/merge/**` |
| MVP-08 | AI-8 | MVP-02 MVP-03 | ⏳ waiting | `DOC-TRACE-001` `MVP-API-001` | `backend/app/api/**` |
| MVP-09 | AI-9 | MVP-08 | ⏳ waiting | `MVP-UI-001` | `frontend/**` |
| MVP-10a | AI-10 | MVP-08 | ⏳ waiting | `MVP-ADAPTER-001` | `backend/app/harness/**` `tools/fake-harness/**` |
| MVP-10b | AI-10 | 10a MVP-05~07 | ⏳ waiting | `MVP-ADAPTER-002` | `backend/app/harness/adapters/**` |
| MVP-11 | AI-11 | MVP-04~10 | ⏳ waiting | 汇总 + `CORE-STATE-COVERAGE-001` | `tests/**` `docs/reports/**` |

★ MVP-04 是瓶颈：同时挡住 MVP-05/06/07/11 四条线。建议配 2 个 AI
（一人做 Claim/幂等，一人做 Lease/fencing/巡检器），在 MVP-02 落地后立即并行。

## 关键路径

~~~text
MVP-01 → MVP-02 → MVP-04 → MVP-05 → MVP-06 → MVP-07 → MVP-11
                  \→ MVP-08 → MVP-10a → MVP-10b
~~~

## 各任务的可提前准备工作（prep）

被阻塞的 AI 不要空等。各自做 prep 项，产出放 `docs/prep/<task-id>/`，**不进主分支**。

| 任务 | 可提前做 | 何时必须完成 |
|---|---|---|
| MVP-02 | schema 草案 DDL、事件 seq 分配器算法说明、"删投影后重建状态"的验证用例设计 | MVP-01 合入后立即 |
| MVP-03 | 状态投影落地、`docs/state-projection.md` 完善、DAG 环检测用例 | MVP-02 前 |
| MVP-04 | 并发 Claim 测试用例、`BEGIN IMMEDIATE` 事务顺序说明、Lease 巡检器触发表 | MVP-02 前 |
| MVP-05 | worktree 短 ID 方案、Windows longpaths 与 Defender 锁文件踩坑预案、分支命名口径 | MVP-04 前 |
| MVP-06 | verify profile 结构、baseline differential 用例、overlay 回收策略 | MVP-05 前 |
| MVP-08 | **OpenAPI 契约草案、错误码表、幂等响应约定**（不依赖 schema，最该提前做） | MVP-02 前 |
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

1. 开工时把状态改成 `doing` 并填开始时间；
2. 提交前跑 `make check`，全绿才可 commit；
3. 完成后必须填 `commit` 与 `验证结果`（贴命令输出，不写"跑通了"）；
4. 遇阻立刻改 `blocked` 并在"已知问题"写清**具体阻塞点**（不要写"有难度"）；
5. **禁止**为了让测试通过而放宽断言或跳过 test_id。
