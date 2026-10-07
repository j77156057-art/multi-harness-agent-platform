### gate-format-repair：收尾——恢复 main 门禁全绿 + 卡标题回填（✅ done）

~~~text
任务编号：gate-format-repair
负责人 Harness：Qoder
开始时间：2026-10-07 20:43
依赖任务：无（协调者「先收尾、门禁全绿后再叠加新架构」指令的第一步；
          win-utf8-stdio（`0ed6e0d`）引入红点，本任务为修复方）
工作区：.worktrees/qoder/
真相源引用：协调者 2026-10-07 指令；backend/requirements-dev.txt（ruff==0.8.6 固定）；
            MVP-PROCESS-002（merge 组：已合入任务必须完成且卡证据完整）
范围声明：
  - **本切片**：①`ruff format` 机械修复 2 个未格式化文件，恢复 main 的 lint 基线
    （纯 formatter 输出，零语义改动）；②5 张**已完成**卡的标题标记回填
    （`🔄 doing`/`doing` → `✅ done`——正文「当前状态」与任务行均已是 done，
    仅 `###` 标题滞后；卡属 WorkBuddy×2、OpenCode×3，依协调者收尾指令执行，
    卡内正文一字不动）；③登记三件套。
  - **显式不做**（记录不静默）：不动 win-utf8-stdio 的卡与任务行（其内容无需变更）；
    不动 mergeguard 的「门禁只跑 docstate+discipline」设计（是否纳入 lint/pytest
    是工具侧决策，另案）；不重构任何测试语义。
允许修改目录：backend/tests/test_gate_utf8.py、backend/tests/test_merge_guard.py、
            docs/tasks/notes/{gate-format-repair,claim-denum-migrate-rest,
            gate-sandbox-pitfall,gate-baseline-repair,root-worktree-clean,
            trace-closure}.md、docs/claims/qoder--gate-format-repair.md、TASKS.md
禁止修改目录：backend/app/**、backend/migrations/**、docs/spec/**、tools/**
交付分支：agent/local/qoder/gate-format-repair/a1
完成标准：
  1. `ruff format --check backend/` 全绿（2 个未格式化文件 → 0）
  2. 5 张卡标题与正文/任务行状态一致（notes 目录 doing 标题清零）
  3. `PYTHONUTF8=1 ./scripts.sh check` 全绿（lint 不再短路，pytest 真实跑完）
test_id：MVP-PROCESS-002
测试命令：PYTHONUTF8=1 ./scripts.sh check
当前状态：done（作者完成，待合入；外部验收待认）
完成时间：2026-10-07 21:00
commit：`edae3c1`（实现提交；合并提交见 git log main）
验证结果：
  docstate    : 90 项检查，0 项失败
  discipline  : 44 项检查，0 项失败
  ruff        : `ruff format --check backend/` 95 files 全绿（修复前 2 个文件
                待格式化；`ruff check` All checks passed）
  pytest      : 364 passed（与 main 在修 lint 前的独立 pytest 跑分一致，
                说明本提交零语义改动），exit 0
  红点定位     : main（`a68be60`）唯一红点 = `ruff format --check` 拒收
                `backend/tests/test_gate_utf8.py` 与 `backend/tests/test_merge_guard.py`；
                漂移来源经 diff 证实是 `0ed6e0d` 本身（合入过程未再改动这两文件）；
                `set -e` 令 `check` 在 lint 处短路 —— 修复前，经 `check` 入口的
                任何人都跑不到 pytest。本次收尾对未修 lint 的 main 独立补跑 pytest：
                **364 passed**；修复分支全量 `check` 复跑：同为 **364 passed**
                （两者一致 → 格式化零语义改动）。
  记录核对     : `claim-denum-coordinator` 的 a1/a2/a3/a4 四条分支全部在 main 上
                （`6aa788e`/`6f9fb73`/`074df23`/`7a56061`）且均在认领 58 的「分支」
                格登记 —— 协调者提到的「a1/a2 合并记录不一致」经核实现已一致
                （由 a2 回填 + a3/a4 补登解决），本任务无需改动其任何文件。
  卡标题回填   : notes 目录 `doing` 标题清零（5 张：claim-denum-migrate-rest /
                gate-sandbox-pitfall / gate-baseline-repair / root-worktree-clean /
                trace-closure —— 正文「当前状态」与任务行均已是 done）。
已知问题：
  - **过程缺口（非本任务范围）**：mergeguard 的合并门禁只跑 docstate+discipline，
    lint/pytest 不在其内；作者的「本地全绿」声明一旦失真（本次：`0ed6e0d` 未过
    formatter 即合入），红点会直达 main，且 `set -e` 短路会掩盖后续步骤。是否把
    lint/pytest 纳入 mergeguard 门禁属 tools/mergeguard 的决策，建议另案（fleet 讨论）。
  - `root-worktree-clean` 卡的 commit 行仍是「本分支 tip 见 git log -1」的指针式
    表述（其作者刻意如此，指向的分支已合入 `a80c5ad` 一线）；本任务按要求只回填
    标题，未改其正文一个字。
~~~
