### gate-baseline-repair：修复 main 门禁基线红 + 消除 detached HEAD 风险（doing）

~~~text
任务编号：gate-baseline-repair
负责人 Harness：OpenCode
开始时间：2026-10-05 10:22
依赖任务：R10-fix-10（✅ done）
工作区：.worktrees/opencode/
真相源引用：AGENTS.md §1（认领登记）、§5（分支与提交）、
            dev-convention.md H-7（工作区隔离）、「会被门禁红掉的」清单
允许修改目录：TASKS.md
禁止修改目录：backend/**、docs/spec/**、tools/**、docs/traceability.md
交付分支：agent/local/opencode/gate-baseline-repair/a1
背景：main 在 b4619a9 上 discipline 仍有 1 项失败（24 项检查，1 失败）——
      `R10-fix-10` 在任务表与认领登记里都标了 ✅ done，但 TASKS.md 里没有对应的
      `### R10-fix-10` 任务卡。该断言会卡住所有 AI 的 ./scripts.sh check。
      同一时间窗内 WorkBuddy 工作区处于 detached HEAD（1b51e2c），H-7 高危组合。
完成标准：
  1. `### R10-fix-10` 任务卡补齐                ⚠️ 作废，见下方撤回说明
  2. `discipline` 24 项检查 0 失败                            ✅
  3. `docstate` 90 项检查 0 项失败                            ✅
  4. WorkBuddy 工作区脱离 detached HEAD，未提交改动零丢失     ✅
  5. 本任务自身也留下任务卡（dogfood 纪律检查器）              ✅
test_id：MVP-PROCESS-001
测试命令：./scripts.sh check（等价分步：docstate + discipline + ruff + pytest）
当前状态：done
commit：df30958（登记）、b4d771c（补卡）、d8e5201（merge main）、本卡收尾
完成时间：2026-10-05 10:31
验证结果：
  - docstate  : 90 项检查，0 项失败
  - discipline: 24 项检查，0 项失败（修复前 24 项 1 失败）
  - ruff check : All checks passed!
  - ruff format: 19 files already formatted
  - pytest     : 54 passed in 0.67s
  - 目录边界  : 仅 TASKS.md（git diff --name-only main...HEAD）
  - detached  : `git -C .worktrees/workbuddy symbolic-ref --short HEAD`
                 → agent/workspace/workbuddy（修复前为 detached HEAD 1b51e2c）

### 过程教训：写卡片之前必须先查 main

完成标准 1 作废的原因值得单独记：**我在补卡之前没有重新确认 main 的状态**。
从「discipline 报缺 R10-fix-10 卡」到「我补完卡准备合入」之间，TRAE 已经用
`R10-fix-10-card` 把同一个缺陷解决了并合入 main（`5a1876e`）。如果我不检查
就合入，同一个 `task_id` 会有两张任务卡 —— 这正是本项目第 3、4 次协作事故的
同构错误（两份真相），而且是我在评审别人时反复指出的那一条。

根因：登记表类缺陷的修复有**天然的竞态**——它是全局单点的（缺一张卡），
而参与者有多个。正确做法是动手前先 `git fetch` + 重新读 TASKS.md 的相关章节，
而不是基于几分钟前的快照开工。

第二条教训：**合并冲突不是异常，是这个仓库的常态**。本任务 merge main 时
TASKS.md 出现 2 处冲突（任务表状态列、认领登记序号），因为 Qoder、TRAE、
WorkBuddy 在同一时间窗内各自改了同一张表两处。冲突本身好解（列取并集、
序号重排），难的是意识到「两张真相」正在被制造。

已知问题：
  - TASKS.md 正被多个 AI 并发编辑，本任务 merge 时已实际发生 2 处冲突。
    H-5 只约定 backend/docs 目录边界，未约定 TASKS.md 的并发写协议。
    建议后续单独评估（例如：任务表/认领登记改为按 task_id 拆文件，或登记
    走独立 API 串行化）。
  - 完成标准 4 涉及移动 `agent/workspace/workbuddy` 引用，超出 H-7 规则 2
    「只移动自己拥有的分支引用」的边界，已事先取得用户确认后执行。
    采用 CAS（compare-and-swap）方式先把该引用快进到工作区当前 HEAD，
    再切分支，保证工作树零变化、未提交改动零丢失。
  - 本任务未修 `fix-flaky-init` 缺卡（它在本任务开工后短暂出现，随后由
    WorkBuddy 自行撤回该任务）。这类「登记了又撤回」的卡片治理无规则可依。
~~~
