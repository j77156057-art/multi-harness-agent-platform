### FORMAL-MULTI-HARNESS-01：多 Harness 本机真实联调

~~~text
任务编号：FORMAL-MULTI-HARNESS-01
负责人 Harness：Codex（协调与修复）；被测 Harness：Codex、Claude Code、OpenCode
开始时间：2026-10-15 +08:00
依赖任务：FORMAL-DISPATCH-01（✅）
工作区：.worktrees/codex/
真相源引用：R10 §6/§7/§9/§11/§13/§17；AGENTS.md §1/§3/§5b
允许修改目录：backend/app/adapter/**、backend/app/git/repo.py、backend/tests/test_formal_multi_harness*.py、
  backend/tests/test_real_adapters.py、
  docs/reports/formal-multi-harness/**、本任务卡/claim、TASKS.md、docs/traceability.md；
  运行期证据写入 data/formal-multi-harness/（gitignored）
禁止修改目录：docs/spec/**、历史封印交付、frontend/**、其他 Harness worktree、生产素材；
  不直接改运行数据库，注册与任务只走管理 API
交付分支：agent/local/codex/FORMAL-MULTI-HARNESS-01/a1
test_id：MVP-API-001、MVP-ADAPTER-003
完成标准：三个已安装 CLI 以各自适配器注册并取得独立 token；三个不同分工的正式任务分别由
  Codex、Claude Code、OpenCode 通过真实 HTTP 领取、心跳、提交并进入可审阅状态；任务不得
  串 Harness、串 attempt 或串工作树；CLI 原始 stdout/stderr、API 回执、事件链、浏览器截图
  与日志留档。若发现适配器/worker 缺陷，先以回归测试钉死，再做最小修复并重跑全套。
测试命令：./scripts.sh check；backend/.venv/Scripts/python.exe -m pytest backend/tests/test_formal_multi_harness*.py；
  真实 CLI 流程按 docs/reports/formal-multi-harness/README.md 复跑
当前状态：done（Codex、OpenCode 与 Claude Code 均有真实成功回执；Claude lane 由平台在 exclusive_paths 检查后代为提交，未启用 bypassPermissions）
commit：`eabac11`（平台代提交逻辑与回归测试；已合入 main）
验证结果：标准 worker 实跑 Claude `FD-132de13296f15a5d.a3`，平台提交 `99e1efe845a41b2bff32bee2354035f51bcdac60`，verify `passed`，进入 `awaiting_review`；定点测试通过。完整证据在 gitignored `data/formal-runs/FD-132de13296f15a5d.json` 与 `data/formal-evidence/FD-132de13296f15a5d/FD-132de13296f15a5d.a3/`。
已知边界：本轮只证明本机 loopback API 与本机已登录 CLI；不声称公网、手机、生产或模型质量验收。三 Harness 成功与失败证据位于 gitignored `data/formal-runs/`；Claude 的平台代提交成功记录为 `FD-132de13296f15a5d.a3` / `99e1efe845a41b2bff32bee2354035f51bcdac60`，verify 已通过。
~~~
