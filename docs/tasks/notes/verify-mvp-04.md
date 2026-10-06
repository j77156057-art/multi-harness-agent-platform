### verify-mvp-04：MVP-04 验收的注入验证补充（✅ done）

~~~text
任务编号：verify-mvp-04
负责人 Harness：WorkBuddy
开始时间：2026-10-05 19:48
完成时间：2026-10-05 20:05
依赖任务：MVP-04（✅ 已验收）
工作区：.worktrees/workbuddy/
真相源引用：R10 §11（Claim 事务与条件 UPDATE）、§13（fencing）、
            §26（CORE-CLAIM-002 / CORE-FENCE-001 的验证要求）
允许修改目录：docs/**（仅 TASKS.md 登记）
禁止修改目录：backend/**（验收对象，只读）、docs/spec/**
交付分支：agent/workbuddy/verify-mvp-04
完成标准：
  1. 四个 test_id 逐条核验，结论可追溯到代码行与测试用例 ✅
  2. 至少做一次注入验证（故意破坏防护 → 确认测试能抓到）✅ 三次
  3. 验收结论与 TRAE 的代码核查结论可对照、不重复 ✅
  4. 验收中发现的问题要么修掉、要么登记为遗留 ✅
test_id：CORE-CLAIM-001、CORE-CLAIM-002、CORE-FENCE-001
测试命令：python tools/discipline/discipline.py && pytest
当前状态：done
commit：9108ca8
验证结果：
  门禁: docstate 90/90、pytest exit 0、ruff exit 0
  注入 1（去掉 rowcount 断言）: 未被抓到，10/10 通过 —— BEGIN IMMEDIATE 写锁已挡双赢家
  注入 2（去掉 status='ready' 条件）: 抓到，2 个并发测试 + 多个 claim 测试失败
  注入 3（禁用 guarded_write epoch 检查）: 抓到，5 个测试失败（含 CORE-FENCE-001 两例）
已知问题：
  - 三次注入中第1 次未被抓到；非缺陷，但需固化「写锁是第一道防线」的认知
  - 建议给 CORE-CLAIM-002 补一条显式用例断言「即使去掉 rowcount 仍只有一个 winner」
  - 复放不重发 lease_token 的问题转 MVP-10b
  详见下方「MVP-04 验收：注入验证补充」小节
~~~
