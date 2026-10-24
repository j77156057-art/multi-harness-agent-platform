### FORMAL-PI-ADAPTER-01：Pi CLI + 本机 Ollama 正式接入

~~~text
任务编号：FORMAL-PI-ADAPTER-01
负责人 Harness：Codex（协调与适配器接线）；成员验收对象：Pi CLI + Ollama 本机模型
开始时间：2026-10-15 10:55 +08:00
依赖任务：FORMAL-MULTI-HARNESS-01（✅）
工作区：.worktrees/pi/
真相源引用：R10 §6/§7/§9/§11/§13/§17；AGENTS.md §0/§1/§3/§5b
允许修改目录：backend/app/adapter/**、backend/tests/test_pi_adapter.py、
  docs/reports/formal-pi-adapter/**、本任务卡/claim、TASKS.md、docs/traceability.md；
  运行证据仅写到 data/formal-pi-adapter/（gitignored）
禁止修改目录：docs/spec/**、其他 Harness worktree、用户既有未提交文件、真实项目素材
交付分支：agent/local/pi/FORMAL-PI-ADAPTER-01/a1
test_id：MVP-ADAPTER-002、MVP-ADAPTER-003
完成标准：Pi adapter 注册与 worker 映射可测；真实 pi CLI 在隔离 attempt worktree 中调用
  本机 Ollama 模型，完成正式 API Claim/Heartbeat/Publish/Submit，并通过 verify 到 awaiting_review；
  原始 CLI 退出码、模型 ID、attempt、commit 与 API/verify 回执留档。未认证/不可用时必须 fail closed。
测试命令：python -m pytest backend/tests/test_pi_adapter.py backend/tests/test_formal_dispatch_worker.py
当前状态：doing
已知问题：Gemini CLI 已安装但缺认证（退出错误码 41）；Pi 默认 agent dir 没有 Ollama 配置，
  需使用每次 worker attempt 独立的临时 models.json 指向 loopback Ollama，不修改用户配置。

适配器移植登记（Mavis，2026-10-24）：合并 OFFICE-UI-01 时发现 **Pi 适配器还停在旧 API**
  （`ADAPTER_TYPES[harness_id]` 那一套：构造器不收 `harness_id`，登记名写死在适配器里）。
  已按 CodexAdapter 的样子移植到新 API：构造器收下 `harness_id` 与 `display_name`，
  登记名用 `replace(REGISTRATION, ...)` 换掉。改动在 merge commit `2c02969` 里
  （那笔合并同时修了 Pi 基座的一处语义缺口）。
  这条登记只说明**适配器已经跟上新 API**；本卡的其余部分仍由 Codex 推进，
  本机模型认证与真实 attempt 验证照旧按原计划走。
~~~
