### split-closure：split 闭环——子任务生命周期 + 唤醒巡检（🚧 doing）

~~~text
任务编号：split-closure
负责人 Harness：Qoder
开始时间：2026-10-07 22:53
依赖任务：adapter-verbs（✅ 已合入 0bce533；split_task 已转正，本卡补其闭环）
工作区：.worktrees/qoder/
真相源引用：R10 §10（`blocked / all_children_merged → ready`「all children merged
            while the parent remains incomplete」；`split` 伪状态 child_created；
            行 587「parent_task_id 语义为父任务依赖子任务」）；§9（计划校验器）；
            §13（stuck 3/5 巡检「无进展」的定义）；ADR-DEV-003（只补缺口）
范围声明：
  - **本切片**（四件，前两件为闭环本体，后两件为闭环落地不可不通的交互）：
    ①`split_task` 同事务创建**子任务计划**（draft `plans` + snapshot，复用
      `create_plan` 的校验/入库逻辑）——子任务不再是惰性 draft，人类走标准
      `plan.approve`（draft→ready 的唯一 R10 入口）即可让它们开工；
    ②`sweep_dependencies` 增第二段唤醒：`blocked` 且为 split 入场且全部子任务
      `merged` → `all_children_merged` → `ready`（清 waiting_since）；
    ③**方向契约修复**：`_materialise_task` 把 `task_dependencies` 方向写反
      （R10 §10 L587：parent=等待方；现存的巡检/测试都是正确方向，只有 plan
      物化链是反的且零覆盖——多任务计划的依赖任务会被 block_timeout 误升级）。
      修复 + 回归测试（旧方向必红）；
    ④**巡检不误杀**：stuck 指纹对 split 父任务纳入子任务活动（事件 seq +
      progress_hash + status），blocked_sla 跳过 split 父任务——否则父任务在
      split 后 3 个巡检周期被 stuck 升级、60 分钟被 SLA 升级，闭环名义存在、
      实际 3 分钟即死。判别式统一：任务最近一次 {split, split_parent_blocked,
      dependency_missing} 事件中后者才不是 split 父任务（覆盖 blocked 起步的
      split 与 report_blocked 后的再 split）。
  - **显式出范围**（记录不静默）：`upstream_invalidated_seq`（revert 影响链写侧）
    仍归 MERGE-REVERT-002——`CORE-SPLIT-001` 从「仅声明」升为「部分有断言」而非
    关闭；子任务 worktree 并发上限计数（R8-01 提及）沿用既有 worktree_capacity
    机制，不在本切片；human 侧 split 路由仍另案。
  - **零迁移**（复用 tasks/plans/plan_snapshots/task_dependencies/task_events）。
允许修改目录：backend/app/tasks/service.py、backend/app/scheduler/**、
            backend/tests/**、docs/traceability.md、TASKS.md、
            docs/tasks/notes/split-closure.md、docs/claims/qoder--split-closure.md
禁止修改目录：backend/migrations/**（零迁移）、backend/app/state/**（只用既有边；
            `all_children_merged` 边已在冻结表）、docs/spec/**、tools/**
交付分支：agent/local/qoder/split-closure/a1
完成标准：
  1. split 后：子任务 draft 带 `plan_snapshot_id`；子计划为 draft 且
     snapshot_hash == plan_hash；`plan.approve` 可把全部子任务推入 ready；
     幂等重放不产生第二份子计划
  2. 唤醒巡检：全部子任务 merged → 父任务 ready + `all_children_merged` 事件 +
     waiting_since 清零；未全 merged / 非 split 入场（report_blocked）不动作
  3. 方向修复：`task_dependencies` 行 (parent=等待方, child=上游) 与 R10 §10
     L587 一致；多任务计划的依赖任务可经 `dependencies_satisfied` 正常唤醒
     （旧代码下必红的回归测试）
  4. 巡检不误杀：split 父任务在子任务有活动（事件/心跳 progress_hash/状态迁移）
     时不被 stuck 计数升级；子任务全静默 3 轮仍升级（死裂分有出口）；
     blocked_sla 不升级 split 父任务
  5. 覆盖套件补 blocked 的 `all_children_merged` 出口（指向新测试）；
     traceability `CORE-SPLIT-001` 升 🟡 并注明缺口；`PYTHONUTF8=1 ./scripts.sh check` 全绿
test_id：CORE-SPLIT-001
测试命令：PYTHONUTF8=1 ./scripts.sh check
当前状态：doing
~~~
