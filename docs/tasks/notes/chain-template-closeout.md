### chain-template-closeout：chain-template 优化收尾（✅ done，结论：撤销）

~~~text
任务编号：chain-template-closeout
负责人 Harness：WorkBuddy
开始时间：2026-10-06 05:15
完成时间：2026-10-06 05:35
依赖任务：无（收尾 MVP-06 的测试提速尝试）
工作区：.worktrees/workbuddy/
真相源引用：R10 §23（SQLite WAL 与备份）、§26（VERIFY-DIFF-001 的验证要求）
允许修改目录：backend/tests/**、docs/claims/**、TASKS.md
禁止修改目录：backend/app/**、docs/spec/**
交付分支：agent/workbuddy/chain-template-rejected
完成标准：
  1. 在空闲环境（0 个 python 进程）测出可信的基线与优化后数据 ✅ 479/525 vs 647
  2. 判定优化是收益还是负收益 ✅ 负收益
  3. 负收益则撤销，不留未验证的改动 ✅ 已撤销
  4. 把否证结果与被纠正的错误定位写进 TASKS.md ✅
test_id：MVP-PROCESS-001
测试命令：./scripts.sh check
当前状态：done
commit：（本次收尾提交）
验证结果：
  基线 479s / 525s，优化后 647s —— 优化是负收益
  单链 28.2s -> 16-20s（真实收益）但全量 647s vs 479s（真实亏损）
  撤销后 backend/tests/chain_template.py 已删除，build_chain 回到基线
已知问题：
  - 真实瓶颈仍未定位。旧cProfile 说「WAL checkpoint 30.1s x 3」，那是 7 进程
    并发污染的数据；空闲环境重测 initialize 只要 0.32s，旧的 30s 来自别处
  - 全量 647s（优化）与 479s（基线）之差仍未解释，怀疑是 git worktree 元数据
    随副本数累积变慢，待后续用 --durations 定位
~~~
