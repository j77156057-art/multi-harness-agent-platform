### FORMAL-DISPATCH-01：本机正式任务分派接通

~~~text
任务编号：FORMAL-DISPATCH-01
负责人 Harness：Codex
开始时间：2026-10-14 +08:00
工作区：.worktrees/codex/
依赖任务：FORMAL-DISPATCH-REGISTER-01
真相源引用：R10 §6/§7/§9/§11/§17；AGENTS.md §1/§3/§5b
允许修改目录：backend/app/adapter/**、backend/app/api/**、backend/app/auth/**、backend/app/claim/**、backend/app/tasks/service.py、backend/migrations/014_formal_registration.sql、backend/tests/test_formal_dispatch*.py、docs/reports/formal-dispatch/**、本任务卡/claim、TASKS.md、docs/traceability.md；office-preview 的 src/formal-dispatch*、server/formal-dispatch*、scripts/verify-formal-dispatch*、vite.config.js、src/main.js、handoff/codex-formal-dispatch-2026-10-14/** 与 ACTIVE-WORK 的本任务登记
禁止修改目录：docs/spec/**、历史封印交付、素材/manifest/verified、其他 Harness worktree；不得裸 SQL 改运行状态
交付分支：agent/local/codex/FORMAL-DISPATCH-01/a1
test_id：MVP-API-001、MVP-ADAPTER-001、DOC-AUTH-001、CORE-CLAIM-001、CORE-CLAIM-002
当前状态：done（实现与作者自检；真实业务任务未批准、未合入）
完成标准：管理员 HTTP 注册 Harness，版本重放幂等/冲突拒绝，历史保留；真实 HTTP 审批/指定成员领取/竞争拒绝/重放/心跳；前端显示真实回执与未接入边界；只报告实际执行的模型/Verify/Review 状态，不用 mock 或直接改库冒充完成。
测试命令：./scripts.sh check；office-preview 的 npm run build / npm run verify 与定点测试
已知问题：ADMIN-01 契约与 ADMIN-03 运行时不在本任务范围；外部 Harness 接入需独立凭据与运行会话，不代为声称在线。
commit：待本任务分支最终提交（实现提交；本卡不伪造 commit）
验证结果：docstate 90/90、discipline 44/44、Ruff check/format 通过；最终全量 pytest 540 passed / 1 dependency warning（975.28s）；定点 API/worker/既有 API 36 项通过；网关 10 项通过；浏览器真实任务 7 项通过；前端 build 与既有 verify 通过。
交付证据：docs/reports/formal-dispatch/DELIVERY.md、VALIDATION.md；office-preview 的 handoff/codex-formal-dispatch-2026-10-14/README.md 与 live-dispatch-result.json。
效力：仅本机 Codex + 独立种子 Git 工作区；结构 Verify 不代替人审，不自动修改 office-preview。
~~~
