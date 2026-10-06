### MVP-03-amend：状态机迁移表对拍整改（✅ done）

~~~text
任务编号：MVP-03-amend
负责人 Harness：TRAE
开始时间：2026-10-05 09:48
依赖任务：MVP-03（代码已由 db9bc19 合入 main，本任务对其验收整改）
工作区：.worktrees/trae/
真相源引用：R10 §10（关键迁移表，86 行/138 个 state-event-next 三元组）、
            §37.7（事件 allowlist）、§9（Plan linter：引用闭合与重复边）
允许修改目录：backend/app/state/** backend/app/plan/** backend/tests/** docs/state-projection.md
禁止修改目录：docs/spec/**（只读；本任务不改 R10 正文）、backend/app/spec.py、tools/**
交付分支：agent/local/trae/MVP-03-amend/a1
背景：TRAE 对 MVP-03 验收时机械对拍代码迁移表与 R10 §10，发现代码缺 25 条边、
      多 11 条 R10 不允许的边（如 escalated|cancel、awaiting_human|escalation_reminder、
      stale/reclaiming 误用 cancel 而非 human.cancel）；validator 未拒绝重复依赖边，
      且重复边会使 indegree 虚高造成 Kahn 误报环。
完成标准：
  1. 代码迁移表与 R10 §10 全量三元组零差异（新增 test_transition_mirror.py 机械对拍）
  2. depends_on 重复边被拒绝，且不再产生假环；有对应用例
  3. state-projection.md 与实际投影（23 状态全量）一致
  4. 全量门禁全绿
test_id：DOC-STATE-001、DOC-PLAN-001
测试命令：./scripts.sh check（等价分步：docstate + discipline + ruff + pytest）
当前状态：done
commit：91e350e（登记先于开工）、b9f140e（修复 + 对拍测试 + 投影文档）
完成时间：2026-10-05 10:02
验证结果：
  - docstate：90 项检查 0 失败（tools/docstate/docstate.py）
  - ruff：check backend 全过；format --check backend 全过
  - pytest：54 passed（新增 test_transition_mirror.py 4 项 +
    test_state_plan.py 重复边 1 项与 23 个参数化边用例；原 26 项不回归）
  - 机械对拍：代码迁移表与 R10 §10 的 138 个展开三元组零差异
    （删 11 条非法边、补 25 条缺失边，均落在 b9f140e）
  - discipline：17 项中 15 过；2 项失败为 main 基线 32f4e45 既有
    （R10-fix-10 进行中：缺任务卡、README/dev-convention 的 docstate
    计数仍写 88 而实际 90），本任务未引入新失败，也未越权修改其范围
已知问题：
  - MVP-03 代码先合入 main（db9bc19）后完成验收，本任务整改也以 amend 形式追入，
    流程偏差已在验收记录中说明。
  - 本分支同样先改后合：合入 main 前对其他 AI 不可见，需 Gate/用户尽快合入。
~~~
