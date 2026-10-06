### fix-flaky-init-card：补录 fix-flaky-init 任务卡（✅ done）

~~~text
任务编号：fix-flaky-init-card
负责人 Harness：TRAE
开始时间：2026-10-05 19:25
依赖任务：fix-flaky-init（仅补登记，不改其交付内容）
工作区：.worktrees/trae/
真相源引用：tools/discipline/discipline.py 的 check_cards（缺卡判定）、
            提交 9d3d3ef 与本文件 flaky 撤回小节（补录事实来源）
允许修改目录：TASKS.md
禁止修改目录：docs/spec/**、backend/**、tools/**
交付分支：agent/local/trae/fix-flaky-init-card/a1
完成标准：
  1. fix-flaky-init 任务卡字段完整（真相源引用、test_id、commit、验证证据）；
  2. discipline 24 项检查 0 失败；
  3. 缺卡违规在违规记录区留档。
test_id：MVP-PROCESS-001
测试命令：.venv\Scripts\python.exe tools\discipline\discipline.py
当前状态：done
完成时间：2026-10-05 19:29
commit：ec12403（登记先于开工）、ce13448（补卡 + 违规留档）
验证结果：
  - discipline：24 项检查 0 失败（修前 23/24，缺卡项已消除）；
  - docstate：90 项检查 0 失败（本任务不改规范与工具，无回归）；
  - 仅改 TASKS.md，无 backend 改动，pytest 85 项/ruff 结论不变。
~~~
