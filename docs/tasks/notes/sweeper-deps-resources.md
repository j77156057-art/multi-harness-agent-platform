### sweeper-deps-resources：依赖巡检与资源巡检（✅ done）

~~~text
任务编号：sweeper-deps-resources
负责人 Harness：Qoder
开始时间：2026-10-07 03:50
依赖任务：sweeper-runner（✅ 已合入 fbc4496）；sweeper-family 设计 §6 第 2 项
工作区：.worktrees/qoder/
真相源引用：docs/proposals/sweeper-family.md §2.1/§2.2（设计）、R10 §10 守卫列
            （dependencies_satisfied / block_timeout / orphan_detected /
            resource_available 四条边的原文）、§11（延迟 Claim 的留痕规则）
O1 裁定：**照字面授予**（协调者 2026-10-07 03:45）：queued 的 resource_available
            完成延迟 Claim 时不检查原请求方存活；其离线则开发租约到期后走既有
            stale/reclaim 链。裁定记录将追加到提案 §7。
允许修改目录：backend/app/scheduler/**、backend/app/claim/service.py（新增
            resume_deferred）、backend/app/config.py、backend/tests/**、
            docs/proposals/sweeper-family.md（仅追加 O1 裁定记录）、
            docs/tasks/notes/sweeper-runner.md（commit 占位语改真哈希）
禁止修改目录：backend/app/lease/**、backend/app/verify/**、backend/app/merge/**、
            backend/app/api/**、backend/app/cli/**、docs/spec/**、tools/**、
            docs/claims/ 下非本任务的文件
交付分支：agent/local/qoder/sweeper-deps-resources/a1
完成标准：
  1. 依赖巡检 `sweep_dependencies`：ready_waiting_deps 三出口逐条实现
     - 全部上游 merged → `dependencies_satisfied` → ready（output 契约部分记录为
       未实现面：MVP 无 output 注册表）
     - 等待超过上游 expected_duration 之和 ×2（缺省按 90 分钟/上游计入，新参数
       expected_duration_default_minutes；快照缺失时同缺省）→ `block_timeout` → escalated
     - 上游全部 terminal 且并非全部 merged → `orphan_detected` → escalated
  2. 资源巡检 `sweep_resources`：queued(resource) 的 `resource_available` →
     claimed：反查延迟项（task_claims 行 + claim_deferred_resource 事件的请求者），
     同一事务内完成资源预占、attempt/epoch/Lease、条件 UPDATE、task_claims 行回填；
     幂等（重复巡检 no-op；原 claim_request_id 回放得到已完成的 attempt，联动
     10b 的 token 轮换）。queued 的 aging_timeout 不在本任务（归 sweeper-aging-stuck）。
  3. RUNNER 注册表更新：SWEEPS 追加两员并更新顺序钉死测试；状态覆盖套件同步：
     ready_waiting_deps 与 queued 移入 RECOVERY，豁免集合重钉（5 → 3）
  4. 测试：三条依赖出口 + 资源恢复（含 O1 场景：请求者到场与否都不影响授予语义，
     离线表现为后续 stale）+ 幂等 + 与 runner 的集成（run_all_sweeps 一次全跑）
  5. PYTHONUTF8=1 ./scripts.sh check 全绿
test_id：SCHED-SWEEP-002
测试命令：PYTHONUTF8=1 ./scripts.sh check
当前状态：done（作者完成，待合入；外部验收待认）
完成时间：2026-10-07 04:1x
commit：（本卡所在提交）
验证结果：
  docstate    : 90 项检查，0 项失败
  discipline  : 46 项检查，0 项失败
  ruff        : All checks passed
  pytest      : 见合并前全量门禁（本任务净增 7 项：test_sweeper_deps_resources.py）
  关键断言     : 依赖三出口（满足/孤儿/超时，超时含"未超不动"对照）；资源恢复
                 （O1 语义：请求者身份来自事件、不查存活；延迟行回填为活 claim，
                 原 claim_request_id 回放得到已完成 attempt；幂等二跑 no-op）；
                 O1 端到端（恢复后请求者离线 → 既有 lease 巡检链 → stale）；
                 runner 顺序钉死更新为三元
已知问题：
  - dependencies_satisfied 的 "output hash 满足契约" 半条 guard 未实现（无 output
    注册表）——按设计 §7 记录，不静默
  - worktree_capacity / merge_capacity 的 queue_reason 目前无生产者（claim 只会
    以 resource 延迟）；对应恢复分支未写，见提案 §2.2
~~~
