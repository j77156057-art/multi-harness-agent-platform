### adapter-verbs：§7 十六动词收满（help/split 三动词转正）（✅ done）

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
当前状态：done（作者完成，待合入；外部验收待认）
完成时间：2026-10-07 22:26
commit：`3c98a77`（实现提交；合并提交见 git log main）
验证结果：
  docstate    : 90 项检查，0 项失败
  discipline  : 44 项检查，0 项失败
  ruff        : All checks passed（96 files already formatted）
  pytest      : 374 passed（本任务净增 10 项：test_adapter_verbs.py），exit 0
  关键断言     : ①request_help 锻造 help_id 并随事件负载落库（R10 守卫「help_id
                已创建」v1 首次落实）；②accept/reject 自环：错 help_id / 非
                needs_help 拒绝且零写入，成功时 owner/attempt/state_changed_seq
                全不动（R10-fix-11），collaborator 记入事件 actor；③split：
                child drafts + depends_on 依赖 + 两事件（伪状态 child_created
                不走 apply_transition）+ in_progress→blocked，blocked 起步只发
                split；同幂等键重放返回首次结果零新事件；stale epoch 拒绝；
                自身/重复/已存在的 child id 拒绝；④十六动词全部接传输层
                （test_adapter / test_real_adapters 联合钉死，UnsupportedVerb
                类已删）；⑤HTTP 端到端：request_help→accept→reject→split（含
                幂等重放）经真实 HttpAdapter 走完。
  覆盖与台账   : 覆盖套件补 needs_help 两自环 + in_progress split_parent_blocked
                出口；traceability §7 差额表 16/16 归零、MVP-ADAPTER-001/-002
                行同步；`UnsupportedVerb` 的「仅名字」缺口关闭。
已知问题：
  - 声明缺口（卡内范围声明逐条对应，均无载体可写而非略过）：`help_artifacts`
    表不存在（§8 清单有、迁移从未建），accept 的「协作者 worktree/Lease」与
    reject 的「提醒 owner」只做事件记账；`help_resolved`（needs_help→
    in_progress）无 §7 动词、无生产者；split 的「budget sufficient」守卫无模
    型可评估（plan 级子任务预算不存在），显式不造近似；子任务为惰性 draft
    （无快照/审批入口），`all_children_merged`（blocked→ready）生产者缺失——
    这一条是 split 闭环的下一块拼图，建议作为并卡（连同子任务生命周期）。
  - human-initiated split（R10 守卫的「human or owner」中的 human 侧）v1 无
    路由；如需应经 §18 面补（另案）。
~~~
