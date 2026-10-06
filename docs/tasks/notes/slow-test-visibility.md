### slow-test-visibility：让慢测试对所有 AI 可见（✅ done）

~~~text
任务编号：slow-test-visibility
负责人 Harness：WorkBuddy
开始时间：2026-10-06 08:45
完成时间：2026-10-06 09:00
依赖任务：无
工作区：.worktrees/workbuddy/
真相源引用：R10 §26（VERIFY-DIFF-001 的验证要求）
允许修改目录：backend/tests/**、backend/pyproject.toml、docs/claims/**、TASKS.md
禁止修改目录：backend/app/**（不碰实现）、docs/spec/**
交付分支：agent/workbuddy/slow-test-visibility
完成标准：
  1. 每次跑 pytest 都能看到慢测试排行 ✅ addopts 加 --durations=15
  2. 超过阈值的测试被点名并给出单独跑的命令 ✅ conftest sessionfinish hook
  3. 阈值可用环境变量覆盖 ✅ SLOW_TEST_SECONDS / SLOW_TEST_TOP_N
  4. pytest -m "not slow" 能跑出快的一轮 ✅ 6s vs 全量 763s
test_id：MVP-PROCESS-001
测试命令：./scripts.sh check
commit：9f122cd
当前状态：done
验证结果：
  - 慢测试汇总（默认 10s 阈值）实测输出：
      2 个超过 10s，合计 64.8s（100% 的测试时间）
        32.86s  tests/test_verify.py::test_the_declaration_refuses_a_stale_epoch
        31.98s  tests/test_verify.py::test_differential_not_applicable_goes_to_a_human
      涉及文件：test_verify.py
      单独跑最慢的一个：pytest "..." -q
  - -m "not slow"：1 passed, 16 deselected in 0.23s（挂钟 6s）
  - 门禁四道全绿，ruff format 后 hook 与 marker 回归验证通过
已知问题：
  - 阈值需按机器调整。默认 10s 在 6 个 AI 并发时会误报一片，
    SLOW_TEST_SECONDS=30 可缓解。并发时的绝对值也不可比——
    同一测试在空闲时和并发时差 3-5 倍是常态。
  - 只标了 test_verify.py 的 16 个用例。其余文件本轮未标
    （ durations 一次跑需要十几分钟，不适合在门禁里做）。
~~~
