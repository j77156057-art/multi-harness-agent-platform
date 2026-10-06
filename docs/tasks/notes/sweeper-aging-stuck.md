### sweeper-aging-stuck：老化、阻塞 SLA 与卡住指纹（✅ done）

~~~text
任务编号：sweeper-aging-stuck
负责人 Harness：Qoder
开始时间：2026-10-07 04:30
依赖任务：sweeper-deps-resources（✅ 已合入 696eea8）；设计 §6 第 3 项
工作区：.worktrees/qoder/
真相源引用：docs/proposals/sweeper-family.md §2.3/§2.4/§2.5；R10 §10 守卫列
            （aging_timeout / block_timeout / stuck_threshold_reached /
            stuck_human_threshold）；§13（三/五次数值）；§20（aging 语义）
允许修改目录：backend/app/scheduler/**、backend/app/api/routes.py、
            backend/app/api/schemas.py、backend/tests/**、TASKS.md、
            docs/tasks/notes/**（本卡）、docs/claims/60-*.md
禁止修改目录：backend/app/claim/**、backend/app/lease/**、backend/app/verify/**、
            backend/app/merge/**、docs/spec/**、tools/**、docs/claims/ 下他人文件
交付分支：agent/local/qoder/sweeper-aging-stuck/a1
完成标准：
  1. `sweep_aging`：ready/queued 等待超过 expected_duration_min × aging_multiplier
     （缺省 2；快照缺省按 expected_duration_default_minutes=90）→ `aging_timeout` →
     escalated。queued 的这条正是 deps-resources 卡里"归 aging-stuck"的那笔。
  2. `sweep_blocked_sla`：blocked 等待超过 block_sla_minutes（60）→ `block_timeout`
     → escalated。
  3. `sweep_stuck`：in_progress/needs_help/blocked/verifying 的指纹
     hash(最新事件 seq, attempt.head_commit, tasks.progress_hash) 存入 sweep_progress；
     连续无变化计数；≥5 → `stuck_human_threshold` → awaiting_human，≥3 →
     `stuck_threshold_reached` → escalated（两条守卫都按字面实现；+1/轮的常规路径下
     3 先触发，5 的可达性写入 docstring）。
  4. progress_hash 链路：HeartbeatRequest 增加可选字段，heartbeat 路由在原事务内
     落 `tasks.progress_hash`；Adapters 已有发送侧，无需改动。
  5. runner 顺序钉死更新为六员（expired_leases → dependencies → resources →
     aging → blocked_sla → stuck）；卡住指纹按序在最终态上判定。
  6. 测试：老化对照组（未超不动/超了才行）、queued 老化、blocked SLA、卡住累计
     与"有进展即清零"（事件追加、progress_hash 变化两种）、双阈值、幂等；
     覆盖套件无需改（新增出口都在已映射状态上）。
  7. PYTHONUTF8=1 ./scripts.sh check 全绿。
test_id：SCHED-SWEEP-003
测试命令：PYTHONUTF8=1 ./scripts.sh check
当前状态：done（作者完成，待合入；外部验收待认）
完成时间：2026-10-07 05:2x
commit：`PENDING`
验证结果：
  docstate    : 90 项检查，0 项失败
  discipline  : 44 项检查，0 项失败
  ruff        : All checks passed
  pytest      : 294 passed（本任务净增 8 项：test_sweeper_aging_stuck.py），exit 0
  关键断言     : 老化对照组（30 分钟不动 / 200 分钟才动，阈值=90×2）；queued 老化；
                 blocked 61 分钟触发、30 分钟不动；卡住计数逐轮断言（基线 0 → 1 → 2 →
                 第 3 次观察触发 escalated 后离开观察集）；事件/新 head/progress_hash
                 三种"有进展"都清零计数；count=4 驱动 stuck_human_threshold →
                 awaiting_human（双阈值字面实现）；heartbeat 路由真实落 progress_hash。
已知问题：
  - stuck 的 5 号阈值在 +1/轮的常规路径下不可达（3 先触发、任务离开观察集）；按字面
    实现两条守卫，可达性注记在 sweep_stuck docstring，不做取舍。
  - 本任务顺带接通了 progress_hash 链路（此前 schema 无字段、API 静默丢弃、列上无
    生产者）；heartbeat 的 progress_hash 现在会落库，属 §7 动词的首次真实生效。
  - 全量门禁本次耗时 28 分钟（fleet 负载），结果不受影响。
~~~
