### plan-evidence-failclosed：evidence source 校验改 fail-closed + 补断言（✅ done）

~~~text
任务编号：plan-evidence-failclosed
负责人 Harness：OpenCode
开始时间：2026-10-05 11:05
依赖任务：MVP-03（✅ 已交付）
工作区：.worktrees/opencode/
真相源引用：R10 §9（方案批准前机械校验器必须检查「evidence source 来自已
            注册能力」「evidence source 能产生判据要求的证据形态」）、
            §16（门禁项只允许 cmd 或 human；judge 不得作为门禁）、
            §26 DOC-PLAN-001、ADR-032（evidence source 必须登记 id、类型、
            命令模板、输出形态、权限和 policy hash）
允许修改目录：backend/app/plan/**、backend/tests/test_state_plan.py、
            docs/traceability.md、TASKS.md
禁止修改目录：docs/spec/**、tools/**、backend/app/spec.py、
            backend/app/state/**、backend/app/db/**、backend/app/claim/**
交付分支：agent/local/opencode/plan-evidence-failclosed/a1
背景：由 `trace-closure` 核实 DOC-PLAN-001 时发现。R10 §9 要求方案批准前校验
      「evidence source 来自已注册能力」，但 `backend/app/plan/validator.py:198-205`
      两处放行：
        L199  `if source is not None:`       —— acceptance 未声明 evidence_source
                                             时完全不检查；
        L202  `elif evidence_sources and ...` —— 注册表为空时跳过未注册检查。
      而 `evidence_sources` 默认是 `frozenset()`。两者叠加的结果是：
      **一个既不声明 evidence_source、注册表又为空的方案，可以通过全部
      evidence 校验**。ADR-032 要求 evidence source 必须登记 policy hash，
      当前实现等于把该登记整体绕过。
      同时 `test_state_plan.py` 只有 3 处 acceptance，覆盖环 / 未注册 capability /
      limits / 重复边 / glob 重复，**没有任何用例覆盖 evidence 校验**。
完成标准：
  1. `check: cmd` 的 acceptance 必须声明 evidence_source，否则拒绝        ✅
  2. 声明的 evidence_source 必须在注册表内；注册表为空即拒绝（不再 fail-open）✅
  3. `check: human` 允许不声明 evidence_source（无判据文件可跑）            ✅
  4. 新增用例覆盖上述三条，且原有合法方案不回归                             ✅
  5. `docs/traceability.md` 的 DOC-PLAN-001 从「3/4 有断言」改为「✅ 有断言」 ✅
  6. `docstate` / `discipline` / `ruff` / `pytest` 全绿                     ✅
test_id：DOC-PLAN-001、MVP-PROCESS-001
测试命令：./scripts.sh check（等价分步：docstate + discipline + ruff + pytest）
当前状态：done
commit：a87e88c（登记）、本卡收尾
完成时间：2026-10-05 11:18
验证结果：
  - docstate  : 90 项检查，0 项失败
  - discipline: 24 项检查，0 项失败
  - ruff check : All checks passed!
  - ruff format: 29 files already formatted
  - pytest     : 89 passed（修复前 85）
  - **注入验证**：把 `validator.py` 单独 stash 回修复前、只保留 4 条新用例，
    `pytest tests/test_state_plan.py` → **2 failed, 32 passed**，红的正是
    `test_plan_validator_requires_evidence_source_for_cmd_acceptance` 与
    `test_plan_validator_rejects_evidence_source_when_registry_is_empty`。
    另2 条（`human` 免evidence、空白串拒绝）是回归守卫，修复前后都该绿。
    注入验证证明这批断言真的咬住了新行为，不是恒真测试。
  - 目录边界  : backend/app/plan/validator.py、backend/tests/test_state_plan.py、
                docs/traceability.md、TASKS.md
已知问题：
  - 本任务把校验改成 fail-closed，属于**收紧**而非放宽。若已有方案依赖
    「不配置 evidence_sources 就能过 linter」，合入后会被拒。这是预期的：
    R10 §9 本来就不允许无登记的 evidence 通过。已确认仓库内现有 3 处
    acceptance 用例均不受影响（L70 `cmd` 带 source、L77 `human` 无 source、
    L196 `judge` 本就该被拒）。
  - 本任务只改**方案期**校验。运行期 Verify 仍按 §16 从 policy allowlist 取
    命令根，证据形态是否真的可产出要等 MVP-06 才能机械验证。本任务不能
    声称「evidence 校验已完备」，只能声称「不再静默放行」。
  - 未处理：ADR-032 要求的 `policy hash` 字段在 `PlanSpec` 上不存在
    （`evidence_sources` 只是 `frozenset[str]`）。补它要改 §8 schema 与
    ADR 正文，超出本任务边界，留给 MVP-05/MVP-06。
  - **与 `trace-closure` 分支的冲突预告**：该分支已把 `DOC-PLAN-001` 改写为
    「⚠️ 3/4 有断言」（记录 fail-open 的中间状态），本分支改写为「✅ 有断言」
    （fail-closed 已落地后的最终状态）。两条分支合并时 `docs/traceability.md`
    的这一行必然冲突，**解法是取本分支的 ✅ 版本**。中间状态已被本卡取代，
    不应保留。
~~~
