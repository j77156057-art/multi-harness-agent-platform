### trace-closure：补 MVP-TRANSITION-MIRROR-001 + 收窄 CORE-STATE-COVERAGE-001（doing）

~~~text
任务编号：trace-closure
负责人 Harness：OpenCode
开始时间：2026-10-05 10:40
依赖任务：MVP-03-amend（✅ done，已合入 main `b4619a9`）
工作区：.worktrees/opencode/
真相源引用：dev-convention.md H-4（没有 test_id 的完成标准不可验收）、
            R10 §26（检查 ID 阶段落点）、§10（迁移表）、§37.7（事件 allowlist）、
            docs/spec/mvp-task-checklist.md MVP-11
允许修改目录：docs/traceability.md、TASKS.md
禁止修改目录：backend/**、docs/spec/**、tools/**、docs/dev-convention.md
交付分支：agent/local/opencode/gate-baseline-repair/a1（与 gate-baseline-repair 同分支，
          见「已知问题」第 1 条）
背景：两处追溯登记与实现脱节。
      (1) `test_transition_mirror.py`（TRAE 于 MVP-03-amend 交付，4 项断言：138 个
          展开三元组双向零差异、展开三元组计数、R10 §10 的状态/事件全部在代码注册、
          表结构诊断为空）没有任何 test_id，只挂在 `DOC-STATE-001` 名下。H-4 明确
          「没有 test_id 的完成标准不可验收」。
      (2) `DOC-PLAN-001` 标注「⚠️ 仅声明 / 未实现（MVP-03 交付）」，但 MVP-03 的
          `backend/app/plan/validator.py` 与 `test_state_plan.py` 已实现其四项要求
          （拒绝环、非法引用、超规模、不可满足 evidence）。该行是过期信息。
完成标准：
  1. `MVP-TRANSITION-MIRROR-001` 登记，并指明 4 项断言分别是什么     ✅
  2. `DOC-PLAN-001` 强制状态改为「✅ 有断言」并指明测试文件          ✅
  3. `CORE-STATE-COVERAGE-001` 收窄到「行为层」并写明与
     `MVP-TRANSITION-MIRROR-001`（声明层）的分工                    ✅
  4. 「现状小结」的 ID 总数与有断言数重新算准                       ✅
  5. `docstate` / `discipline` / `ruff` / `pytest` 全绿              ✅
test_id：MVP-PROCESS-001、MVP-TRANSITION-MIRROR-001
测试命令：./scripts.sh check（等价分步：docstate + discipline + ruff + pytest）
当前状态：done
commit：412e7f2（登记）、本卡收尾
完成时间：2026-10-05 10:52
验证结果：
  - docstate  : 90 项检查，0 项失败
  - discipline: 24 项检查，0 项失败
    （登记后、登记 `MVP-TRANSITION-MIRROR-001` 之前，traceability 组曾当场报出
      `未登记=['MVP-TRANSITION-MIRROR-001']` —— 工具对自己生效）
  - ruff check : All checks passed!
  - ruff format: 29 files already formatted
  - pytest     : 85 passed in 4.26s
  - 目录边界  : docs/traceability.md + TASKS.md
    （git diff --name-only main...HEAD）
  - 数字复核  : 29 个 ID / 10 有断言 / 1 部分 / 18 仅声明，
    按四张表逐行点数得出
已知问题：
  - 本任务与 `gate-baseline-repair` 共用同一交付分支。原因是 TASKS.md 正在被
    多个 AI 并发编辑（本分支两次 merge main 都产生冲突），合并次数越少，
    冲突窗口越小。这是对并发写协议的临时规避，不是推荐做法。
  - 本任务只改追溯登记，**不新增任何断言**。`MVP-TRANSITION-MIRROR-001` 指向的
    4 项断言已由 MVP-03-amend 写好，本任务只是给它们一个 ID。登记表声称的
    「✅ 有断言」等级不因此变高——`traceability.md` 自己写明「强制状态列本身
    不被断言，判断某个 ID 是否真的覆盖了它声称的行为仍需人读」。
  - 未处理：`CORE-SPLIT-001` 标注「未实现（MVP-03/04）」。核对后确认该标注
    **正确**——MVP-03-amend 只补了 `split_parent_blocked` / `all_children_merged`
    两条边，代码里没有任何创建子任务、继承预算、写父子依赖边的逻辑
    （`parent_task_id` 只是投影上的字段）。故不在本任务范围内改动。
~~~
