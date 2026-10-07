### sweeper-workers：verify / review / merge 的等待与租约重排（✅ done）

~~~text
任务编号：sweeper-workers
负责人 Harness：Qoder
开始时间：2026-10-07 06:10
依赖任务：sweeper-aging-stuck（✅ 已合入）；设计 §6 第 4 项
工作区：.worktrees/qoder/
真相源引用：docs/proposals/sweeper-family.md §2.6/§2.7；R10 §10 守卫列
            （verify_lease_expire / verify_queue_timeout / review_lease_expire /
            merge_lease_expire / merge_timeout）与边注（L515：重排不新建 attempt、
            不改 epoch）；§13（review/merge 等待只升级不抢占）；§16（verify 租约）
O2 裁定（协调者 2026-10-07）：verify_lease_expire 的「对账确认进程已死」以
            `verify_jobs` 为代理；MVP 近似如下，**将写入每个 sweep 的 docstring 与卡**：
            - 活跃判据 = job 行 status='running'（MVP worker 同步执行，崩溃会留下
              running 假状态）；无 pid/pgid/boot_id 生产者（列在、没人写）——
              不做假验尸；
            - 时间锚 = 该任务最新事件（submit_result）的 created_at（job.created_seq
              在 MVP 写入时为 NULL，见 verify/worker.py 的 INSERT）。
允许修改目录：backend/app/scheduler/**、backend/tests/**、TASKS.md、
            docs/tasks/notes/**（本卡）、docs/claims/61-*.md
禁止修改目录：backend/app/verify/**、backend/app/merge/**、backend/app/claim/**、
            backend/app/lease/**、backend/app/api/**、docs/spec/**、tools/**、
            docs/claims/ 下他人文件
交付分支：agent/local/qoder/sweeper-workers/a1
完成标准：
  1. `sweep_verify_lease`：verifying + 当前 attempt 有 running 的 verify job +
     锚龄 > verify_lease_seconds → 旧 job 置 `aborted`、**新建 job（queued）**、
     自环 `verify_lease_expire`（不新建 attempt、不改 epoch，守 §10 边注）+
     O2 近似声明。
  2. `sweep_verify_queue`：queued 的 verify job 超 verify_queue_sla_minutes →
     `verify_queue_timeout` → escalated。**生产者注记**：MVP 不产生 queued job
     （ADR-030 队列未实现），条件按字面实现、测试直接播种子行；不许静默。
  3. `sweep_review_lease`：awaiting_review + reviews 行 status='claimed' 且
     lease_until 已过 → 该行置 'expired'，自环 `review_lease_expire`（等待时钟
     不重置）。守卫中「无进行中 review intent」在 MVP 恒真（intent 事后记账，
     无 pending 生产者）——注记。
  4. `sweep_merge_recovery`：merging + 锚龄（最新 merge_start 事件）超过
     **merge_lease_seconds → `merge_lease_expire` → 回 approved（重排，worker 崩溃
     场景）；否则超过 integration_test_timeout_minutes → `merge_timeout` →
     escalated**。两阈值默认相同（30m），顺序=先重试后升级；升级窗口在
     lease > test_timeout 的配置下存在——分窗依据写入 docstring。approved 的
     merge_lease_expire 自环为 no-op，记录不实现。
  5. runner 顺序钉死扩为十字（六员 + verify_lease/verify_queue/review_lease/
     merge_recovery；workers 组放最后，理由：先让状态类巡检收束再动等待类）。
  6. 测试：占位种子 + 时钟注入覆盖五条边 + 对照组 + 幂等；verify 重排断言
     旧 job aborted/新 job queued/attempt 与 epoch 不变。
  7. PYTHONUTF8=1 ./scripts.sh check 全绿。
test_id：SCHED-SWEEP-004
测试命令：PYTHONUTF8=1 ./scripts.sh check
当前状态：done（作者完成，待合入；外部验收待认）
完成时间：2026-10-07 07:4x
commit：`901242a`（实现提交；合并提交见 git log main）
验证结果：
  docstate    : 90 项检查，0 项失败
  discipline  : 44 项检查，0 项失败
  ruff        : All checks passed（90 files formatted）
  pytest      : 307 passed（本任务净增 9 项：test_sweeper_workers.py），exit 0
  关键断言     : verify 重排（旧 job aborted/新 job queued/attempt 与 epoch 不变）；
                 verify 队列 SLA 与对照组；review 到期置 expired + 自环、未到期不动；
                 merge 双阈值分窗（默认同值时先重试；monkeypatch lease=2h 时 45 分钟
                 落在升级窗）；幂等二跑 no-op。
已知问题：
  - **O2 近似已按裁定显式声明**（sweep_verify_lease docstring）：无 pid/pgid/boot_id
    生产者、不做假验尸；锚=任务最新事件时间。verify_queue 的 queued 生产者、
    review 的 review intent、merge 的 pending intent 同为"列在、无生产者"，逐处注记。
  - approved 的 merge_lease_expire 自环是 no-op，记录不实现。
  - 测试期撞出一条既有不变量：task_events 的 append-only 是**触发器强制**的——本来
    想回拨事件时间做锚点，被数据库拒绝；改用仓库既定模式（推 now 不动时间字段）。
~~~
