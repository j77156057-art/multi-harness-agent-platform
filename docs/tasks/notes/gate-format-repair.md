### gate-format-repair：收尾——恢复 main 门禁全绿 + 卡标题回填（🚧 doing）

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
当前状态：doing
~~~
