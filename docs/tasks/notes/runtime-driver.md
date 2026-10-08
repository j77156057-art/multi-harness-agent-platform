### runtime-driver：Verify/Merge 接入正式运行环（🚧 doing）

~~~text
任务编号：runtime-driver
负责人 Harness：Qoder
开始时间：2026-10-08 13:05
依赖任务：e2e-harness-drill（✅ f54420c；F6 发现来源）
工作区：.worktrees/qoder/
真相源引用：R10 §16（Verify 由独立池执行、verify_jobs 至少字段、并发上限 3、
            env/code 失败分流、receipt 门禁）；§21（Merge Queue 准入=gates 满足
            + integration_health green、容量与背压、串行集成写者）；L542（receipt
            通过前 Reviewer 不得 claim）；L899（Verify 与 Review 串行）；
            §7 十六动词（harness 侧唯一入口=外部主体，本卡不碰）
范围声明：
  - **现状（drill F6 实证）**：`VerifyWorker`/`MergeWorker` 是完整类但**只有
    测试与操作面脚本在调用**；sweeper 家族只维护状态机（verify_lease/
    verify_queue 的 SLA、merge_recovery 的恢复），verify_jobs 的 queued/lease
    列**没有生产者**（sweeps.py 内已记录）；merge 无队列实现（仅
    `merge_queue_max_length` 配置与 §21 规范）。生产运行环缺位。
  - **本切片（运行环 v1）**：新模块 `app/runtime`：
    ①**scan**：verify 候选=`verifying` 状态任务（取 current attempt 的
      commit/base；**profile 名取自任务 plan snapshot 的
      `verify_profile` 字段**；已有「该 attempt+commit+profile 通过 receipt」
      者跳过）；merge 候选=`approved` 任务（commit=attempt.head_commit、
      branch 取自 workspaces）。
    ②**execute**：verify 走 `ThreadPoolExecutor(max_workers=concurrent_verify,
      默认 3；每线程独立 Database 连接)`；merge **逐轮串行**（integration
      单写者）。策略来自 `AP_RUNTIME_POLICY_FILE`（JSON：command_roots +
      profiles + integration profile）——VerifyPolicy/merge policy 复用现成
      `from_mapping`；文件缺失时**拒绝执行并说明**（不猜命令）。
    ③**CLI**：`python -m app.runtime [--check|--once|--loop]`（--check 只读
      清单+策略状态；--once 一轮；--loop 按 AP_RUNTIME_INTERVAL 默认 10s）。
    ④**幂等**：verify 前置跳过已通过 receipt；merge 由 MergeWorker 自身门禁/
      状态挡重复——双跑 `--once` 第二轮 0 动作（测试钉死）。
    ⑤**Review 侧裁决（如实）**：无且不需要 ReviewWorker 类——审查是外部
      harness 主体行为（review.claim/submit 已是 API），平台侧=队列/SLA/§16
      串行门禁（已有）；本卡运行环**不驱动 review**，测试断言 review 行数不变。
  - **显式出范围**（记录不静默）：完整的 §16 verify job 生命周期（queued
    生产者、verify Worker Lease/心跳、崩溃原地重排的 job 级语义）——v1=直接
    执行+幂等跳过，现有 verify_lease/verify_queue/merge_recovery 巡检兜底；
    完整化列后续卡；**acceptance→verify 命令编译**（R10 §8 acceptance 是
    verify 命令的正规来源，现走 policy profile）——归 F11 收紧卡；§21 的
    baseline_receipt/flaky registry/stop-the-line 全量——后续；start.bat 集成
    runtime 窗口——后续微调（不动已合入的 start.bat）；hotfix 抢占——后续。
  - **零迁移**（全部现有表）。
允许修改目录：backend/app/runtime/**（新）、backend/app/config.py（策略文件
            路径与轮询周期）、backend/tests/**、README.md（启动段补一行）、
            TASKS.md、docs/traceability.md、docs/tasks/notes/runtime-driver.md、
            docs/claims/qoder--runtime-driver.md
禁止修改目录：backend/app/state/**、backend/app/verify/**、backend/app/merge/**
            （v1 只驱动不改这两个 worker 的内部）、backend/migrations/**、
            docs/spec/**、frontend/**
交付分支：agent/local/qoder/runtime-driver/a1
完成标准：
  1. **scan 正确**：verifying 任务被识别（attempt/commit/profile 来源断言）；
     已有通过 receipt 的 verifying 任务被跳过；approved 任务为 merge 候选；
     profile 缺失（快照无字段）→ 显式不执行并记录（不猜）
  2. **verify 实测**：真链（claim→start→commit→publish→submit）后**不手工
     调 worker**——`--once` 把任务送到 awaiting_review（receipt 落库）
  3. **merge 实测**：review approve 后 `--once` → merged、集成分支前进
  4. **并发上限**：4 个 verifying 任务一轮只执行 3（concurrent_verify），
     第 4 个留待下轮；merge 候选逐轮串行（一轮一个）
  5. **幂等双跑**：同一状态连跑两轮 → 第二轮 actions=0（无重复 receipt/重复
     merge）
  6. **review 不被驱动**：跑前后 reviews 行数不变（边界如实在卡内声明）
  7. **CLI/策略**：--check 只读（exit 0/2）；策略文件缺失 → verify/merge
     拒绝执行且信息明确（exit 2 语义）；--once 输出 JSON 行
  8. `PYTHONUTF8=1 ./scripts.sh check` 全绿；traceability 新增
     `CORE-RUNTIME-001`
test_id：CORE-RUNTIME-001（新登记）
测试命令：PYTHONUTF8=1 ./scripts.sh check
当前状态：doing
~~~
