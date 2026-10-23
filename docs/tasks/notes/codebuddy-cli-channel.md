### codebuddy-cli-channel：CodeBuddy 正式通道三处真缺陷（✅ 修完待合）

~~~text
任务编号：codebuddy-cli-channel
负责人 Harness：OpenCode
开始时间：2026-10-17
依赖任务：FORMAL-MULTI-HARNESS-01（该任务把 codebuddy-member-1 登记进正式名单，
          这条通道的故障就是从它身上第一次被观测到）
工作区：.worktrees/codex/
真相源引用：R10 §25（阶段 5 harness 适配：argv 契约与输出解析）、§11（Claim 失败
            必须留下可判读的失败原因，不能把"空输出"当成"没干活"）
允许修改目录：backend/app/adapter/harnesses/{codebuddy,cli}.py、backend/tests/test_real_adapters.py
禁止修改目录：其余 app/adapter/**（其它 CLI 的 argv 自有其契约，本轮不动）
交付分支：agent/local/opencode/codebuddy-cli-channel/a1（尚未切出）
完成标准：
  1. 工作单不再被变参 `--add-dir` 吞掉：真实 CLI 收到提示词（转写里出现
     `<user_query>…</user_query>`）并返回非空 `result`。
  2. `parse_output` 对"整份 JSON 数组"与"逐行 NDJSON"两种形状都解析出正确的
     `final_message` 与 `session_id`。
  3. 工作单**完整**送达 CLI，不被多行截断：位置参数在多行时只保留第一行（真实
     转写 `<user_query>Line1 alpha</user_query>`），所以改走 stdin；runner 只在适配器
     声明 `prompt_via_stdin` 时建管道，其余 CLI 仍拿 DEVNULL。
  4. 回归测试是**能失败**的：改坏前的 argv 被同一个复现器判为丢提示词；样本转写被
     证明真的不是逐行可解；多行样本被逐字节比对。
test_id：`MVP-ADAPTER-003`（harness 适配与输出解析面）
测试命令：
  cd backend && ../.venv/Scripts/python.exe -B -m pytest tests/test_real_adapters.py -q
当前状态：doing —— 三处缺陷已修 + 9 条回归/传输测试绿 + `test_real_adapters.py` 全绿；
        真实 CLI 复跑取证通过；线上 `FD-dd3a044074777d1d` 跑通「提交 → 跨提供方
        审查 → 返修」闭环；待 Merge Gate。
完成时间：（未完成）
commit：（未提交，与 R10-fix-13 同工作区）
验证结果：
  真实 CLI rc=0，转写 17736 字节，`parse_output` → {"session_id": "01a148f6-…",
  "final_message": "OK"}；`ruff check .` = All checks passed!；`ruff format --check .` 干净。
  线上：`FD-dd3a044074777d1d.a1` 0e1dada1 / rc-24b3efc1 → 跨提供方 request_changes；
  `a2` 5acdda3d / rc-b07b9c94 → awaiting_review，返修 goal 命中全部 4 条意见原文。
  全量门禁：pytest 737 用例 RC=0；docstate 98/0；discipline 44/0；ruff 全绿。
已知问题：
  1. 第二轮修的解析缺陷**会被第一轮的修法掩盖** —— 只改 argv 就以为通道通了，实际
     仍会把 JSON 碎片记成"harness 的消息"。两处必须一起修，已在认领文件里写明因果。
  2. 同一个道理重复了第二次：把工作单挪到变参之前看起来"对了"，但多行位置参数会被
     截到第一行，模型看不到任务规格。判据必须是"CLI 转写里的 `<user_query>` 是否等于
     完整工作单"，而不是"rc 是否为 0"。
  3. 单行 NDJSON 形状在现行版本没有实测样本（现有单行测试是既有的假 stdout），
     保留它是因为那属于文件名承诺的契约面。
  4. `codebuddy.CMD` 在 Windows 上需要 Git Bash 才启用 `/enter-worktree` 等特性，
     本机未装（CLI 自己打印的提示）。不影响本缺陷。
~~~
