### FORMAL-DISPATCH-REGISTER-01：串行登记正式接通任务

~~~text
任务编号：FORMAL-DISPATCH-REGISTER-01
负责人 Harness：Codex
开始时间：2026-10-14 +08:00
工作区：.worktrees/codex/
依赖任务：无
真相源引用：AGENTS.md §1/§3/§5b；dev-convention H-4/H-7/H-8
允许修改目录：TASKS.md、docs/claims/codex--FORMAL-DISPATCH*.md、docs/tasks/notes/FORMAL-DISPATCH*.md；登记门禁的 Windows 前置修复 tools/mergeguard/guard.py 与 backend/tests/test_formal_dispatch_guard.py
禁止修改目录：业务代码、docs/spec/**、其他 Harness worktree、运行数据库
交付分支：agent/local/codex/FORMAL-DISPATCH-REGISTER-01/a1
test_id：MVP-PROCESS-001
当前状态：done
完成标准：任务/claim/卡一致，经全量 check 与受控 Merge Gate 将认领合入 main；未合入不实施。
验证结果：docstate 90/90、discipline 44/44（根用户未跟踪文件告警）、ruff check/format 通过、全量 pytest 530 passed / 1 dependency warning（956.27s）；进程探测与 mergeguard 定点 46 passed。未冒充功能验收。
commit：本任务分支登记提交（合入后可由默认 merge 标题追溯）
已知问题：业务草稿已暂存；登记不接受该草稿、不改变 ADMIN-01/03。
~~~

H-8：已逐工作区检查 status 与 main..分支变更；WorkBuddy 仅有未跟踪的
ADMIN-03 prep，其他旧分支没有未提交共享编辑。本轮不合入它们的差异。

登记门禁前置修复：Windows 的 os.kill(pid, 0) 会终止被探测进程，
导致全量 pytest 两次中断（一次 268 passed 后 KeyboardInterrupt）。
修复仅替换无副作用的 Win32 查询，不关闭/跳过任何门禁。
