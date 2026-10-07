### adapter-verbs：§7 十六动词收满（help/split 三动词转正）（🚧 doing）

~~~text
任务编号：adapter-verbs
负责人 Harness：Qoder
开始时间：2026-10-07 21:37
依赖任务：paperclip-buzz-adr（✅ 已合入 8b6d98f；八步计划「只补真缺口」的第一项，
          协调者选定选项 a）
工作区：.worktrees/qoder/
真相源引用：R10 §7（accept_help(task_id, help_id) / reject_help(task_id, help_id) /
            split_task(task_id, child_specs, idempotency_key)）；§10 边
            （in_progress → request_help → needs_help；needs_help 三条自环；
            split 伪状态 child_created；split_parent_blocked → blocked）；
            R10-fix-11（needs_help 自环只写 help 协作元数据，不写 owner/attempt）；
            §11（幂等键）；ADR-DEV-003（R10 唯一业务真相源）
范围声明：
  - **本切片**：三动词的服务实现 + HTTP 路由 + HttpAdapter 转正（UnsupportedVerb
    清零，test_adapter / test_real_adapters 断言同步）；`request_help` 补产
    `help_id`（R10 守卫「help_id 已创建」在 v1 从未落地——本次补上，随
    REQUEST_HELP 事件负载落库）；split 用 `command_idempotency`（publish 同款
    模式）实现「幂等键有效」；traceability 的 §7 差额表归零。
  - **显式出范围**（记录不静默；均系 spec 有表/边但本仓库尚无载体的部分）：
    ① `help_artifacts` 表不存在（§8 清单里有、迁移从未建）→ help_accepted 的
    「创建协作者 worktree/Lease」副作用不做（leases.kind 的 CHECK 也没有 help
    类），只事件记账；② `help_rejected` 的「提醒 owner」无提醒载体，只事件；
    ③ `help_resolved`（needs_help → in_progress）无 §7 动词、无生产者，维持现状；
    ④ split 的「budget sufficient」守卫无对应模型（plan 级子任务预算不存在），
    显式声明"不可评估"，不造近似；⑤ 子任务生命周期（draft 子任务无快照、
    无审批入口）与 `all_children_merged`（blocked → ready）生产者不在本切片；
    ⑥ child_specs 的字段集除 `id` 外只透传记录，不强校验。
  - **零迁移**（复用 tasks / task_dependencies / command_idempotency 三张表）。
允许修改目录：backend/app/tasks/service.py、backend/app/api/**、
            backend/app/adapter/http.py、backend/tests/**、docs/traceability.md、
            TASKS.md、docs/tasks/notes/adapter-verbs.md、docs/claims/qoder--adapter-verbs.md
禁止修改目录：backend/migrations/**（零迁移）、backend/app/state/**（只用既有边，
            不改迁移表）、docs/spec/**、tools/**、backend/app/verify/**、
            backend/app/merge/**
交付分支：agent/local/qoder/adapter-verbs/a1
完成标准：
  1. `accept_help` / `reject_help`：needs_help 态 + help_id 与最近一次 request_help
     记录一致才收；自环边只发事件（不写 owner/attempt/state_changed_seq，
     R10-fix-11）；错误 help_id / 非 needs_help 态拒绝
  2. `split_task`：owner §13 fence + 状态限 in_progress/blocked + 子任务 id 不
     得已存在；产物 = draft 子任务 + task_dependencies(depends_on) + `split`
     事件（伪状态 child_created，不走 apply_transition）+ in_progress 时
     `split_parent_blocked` → blocked；同幂等键重放返回首次结果、无新事件
  3. 路由 `/adapter/{accept_help,reject_help,split_task}`；HttpAdapter 三动词
     实现（UnsupportedVerb 集合为空）；test_adapter / test_real_adapters 断言同步
  4. 测试：三动词服务级矩阵（成功/拒绝/幂等重放/自环零字段写入）+ HTTP 端到端
     （经真实 adapter 对象）；覆盖套件补 needs_help 两自环与 in_progress
     split_parent_blocked 的出口登记
  5. traceability §7 差额表与 MVP-ADAPTER-001 行同步（缺口清零）；
     `PYTHONUTF8=1 ./scripts.sh check` 全绿
test_id：MVP-ADAPTER-001
测试命令：PYTHONUTF8=1 ./scripts.sh check
当前状态：doing
~~~
