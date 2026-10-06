### fix-wal-lock-retry：Windows WAL 锁重试修复（✅ 已完成）

~~~text
任务编号：fix-wal-lock-retry
负责人 Harness：WorkBuddy
开始时间：2026-10-05 09:12
完成时间：2026-10-05 09:20
依赖任务：MVP-02（✅ 已验收）
工作区：.worktrees/workbuddy/
真相源引用：R10 §8（canonical schema）、§13（Lease/并发写入）、§15（事件顺序）、
            docs/dev-convention.md H-7（隔离与验证）
允许修改目录：backend/app/db/** backend/app/events/** backend/migrations/**
禁止修改目录：backend/app/spec.py、docs/spec/**、tools/docstate/**
交付分支：agent/workbuddy/fix-wal-lock-retry
完成标准：
  1. Windows 多进程 WAL 锁竞争在有限超时内重试，成功或返回明确超时错误 ✅
  2. 锁文件打开失败与加锁失败均纳入可重试错误集合 ✅
  3. 并发初始化与完整测试门禁通过 ✅
test_id：CORE-RECOVER-001
测试命令：./scripts.sh check
当前状态：✅ 已完成
commit：403e3a5
验证结果：
  docstate   : 88 项检查，0 项失败
  ruff check : All checks passed
  ruff format: 通过
  pytest     : 20 passed
已知问题：
  - Windows 锁行为仍需在实际目标环境持续复测；本卡记录的是当前工作区的验证证据。
~~~
