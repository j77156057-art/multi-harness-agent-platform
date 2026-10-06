### sweeper-family：巡检器家族设计（✅ done，提案待裁决）

~~~text
任务编号：sweeper-family
负责人 Harness：Qoder
开始时间：2026-10-06 11:05
依赖任务：MVP-11（✅ 已合入 bef5f25；其终审报告 §6-P3-1 圈定本设计范围）。
          任务表依赖列写「—」是台账要求：过程任务不进正式关键路径链
          （declared path 9 节点的机制约束），真实依赖以本行为准。
工作区：.worktrees/qoder/
交付物：docs/proposals/sweeper-family.md —— **只交付设计，不含实现**
真相源引用：R10 §10（迁移表守卫列：block_timeout/orphan_detected/aging_timeout/
            stuck_*/verify_queue_timeout/verify_lease_expire/review_lease_expire/
            merge_lease_expire/merge_timeout/review_wait_timeout/
            review_round_exhausted/human_wait_timeout/escalation_reminder）、
            §13（巡检默认参数与双锚点；「三次巡检无进展」定义）、
            §20（aging 与 escalated 策略）、§16（verify 队列 SLA）、§21（merge 超时）、
            §25 阶段 2、MVP-11 终审报告 §3.2/§6-P3-1
允许修改目录：docs/proposals/**（新增 sweeper-family.md）、docs/traceability.md、
            TASKS.md（本卡与任务行）
禁止修改目录：backend/**（设计任务不动实现）、docs/spec/**、tools/**、
            docs/claims/ 下非本任务的文件
交付分支：agent/local/qoder/sweeper-family/a1
完成标准：
  1. 逐事件规格：R10 中每个"时间/资源条件→自动转移"的出口都有触发谓词、动作、
     参数、幂等/并发要求、测试注入点的完整规格（含现值核对：config.py 已有参数
     与缺失参数清单）
  2. 运行器架构：单循环 vs 多循环的选型与理由；时钟与重启语义；与
     expire_stale_leases 原型的关系；"连续 N 次巡检无进展"的指纹载体设计
  3. 与状态覆盖套件的对接：实现后 test_state_coverage 的哪些豁免被点亮、
     反向守卫如何强制（逐态列出）
  4. 任务拆分建议：实现按巡检器分组的任务卡草案（test_id / 依赖 / 边界），
     cancel 家族与人类操作面显式划出"非巡检器"边界
  5. 风险与开放问题：竞态、时钟跳变、verify_run 对账、block SLA 取值等
test_id：SCHED-SWEEP-001（设计阶段编号；实现类测试 id 由实现任务落地）
测试命令：PYTHONUTF8=1 ./scripts.sh check（文档门禁）
当前状态：done（作者完成，待合入；**设计采纳与否待评审裁决**）
完成时间：2026-10-06 11:40
commit：（本卡所在提交）
交付物：docs/proposals/sweeper-family.md（约 230 行）—— 10 组巡检查规格表、
        运行器架构（单循环/时钟/并发/幂等）、参数清单（现有 vs 新增 10 项）、
        覆盖对接（豁免 7→3）、实现任务拆分（5+1）、风险与 3 个开放问题（O1/O2/O3）
验证结果：
  docstate    : 90 项检查，0 项失败
  discipline  : 44 项检查，0 项失败（本任务把依赖列写作「—」，避免过程任务
                拉长声明的关键路径 9 节点——这是门禁的机制约束，已在卡内注明）
  评审抓手     : R10 守卫列逐条对照（§2 每行引原文）；开放问题 O1（离线请求者的
                延迟 Claim 归属）、O2（verify 进程对账的近似）、O3（漂移指纹冻结值
                落点）需要裁决后才能开对应实现任务
~~~

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
