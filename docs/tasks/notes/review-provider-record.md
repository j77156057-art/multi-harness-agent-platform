### review-provider-record：审查行没记住提供方（已修待合）

~~~text
任务编号：review-provider-record
负责人 Harness：OpenCode
开始时间：2026-10-17
依赖任务：auth-layer（跨提供方规则与身份面）、R10-fix-12（review lease 面）
工作区：.worktrees/codex/
真相源引用：R10 §21（review_policy=cross_provider：双方提供方必须已知）、
            §6（提供方以注册面为准，不接受自报）
允许修改目录：backend/app/tasks/service.py、backend/tests/test_review_findings.py
禁止修改目录：app/auth/policy.py（权限矩阵本轮不动）；其余 harness 的认领/任务卡
交付分支：agent/local/opencode/review-provider-record/a1（尚未切出）
完成标准：
  1. 新产生的 harness 审查行写明注册面给出的 provider，不再一律写 "unknown"。
  2. 人工审查仍记 "human"；查不到注册行时降级 "unknown"，不猜。
  3. 三条测试分别覆盖"记录真实 provider / 降级 / human"，且都能失败。
test_id：`MVP-ADMIN-REVIEW-001`
测试命令：
  cd backend && ../.venv/Scripts/python.exe -B -m pytest tests/test_review_findings.py -q
当前状态：doing —— 修复完成、测试绿；线上已用真实「领取审核」复核（新行记 qoder）。
完成时间：（未完成）
commit：（未提交，与 codebuddy-cli-channel 同工作区）
验证结果：
  test_review_findings.py 9 通过；全量 737 用例 RC=0；docstate 98/0；
  discipline 44/0；ruff check + format 全绿。
  线上：rv-817cfd3f049347f7852252cec517ce42（真 UI 领取）记 reviewer_provider=qoder，
  修复前的 rv-1fb669… 记 unknown。
已知问题：
  1. 这一列目前没有读者 —— 修复的意义是"历史记录不再写错"，不是"修掉一个算错的
     结果"。真正的风险留给将来读它的门禁，本任务只保证数据是对的。
  2. 已写入的旧行仍是 "unknown"（不做回填：回填需要按 reviews.reviewer_harness_id
     反查注册面，属于数据迁移，不在本切片里擅自做）。
~~~
