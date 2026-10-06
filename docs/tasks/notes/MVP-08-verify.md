### MVP-08-verify：MVP-08 验收（✅ done，通过附遗留清单 4 项）

~~~text
任务编号：MVP-08-verify
负责人 Harness：TRAE
开始时间：2026-10-05 20:49
依赖任务：MVP-08（3de2115 已合入 main，状态 review，先合后验）
工作区：.worktrees/trae/
真相源引用：docs/spec/0.3-R10.md（§7/§21 REST API 与错误码、§11 幂等、§26 test_id）、
            docs/prep/MVP-08/openapi.yaml、error-codes.md、
            TASKS.md MVP-08 卡、backend/app/api/**、backend/tests/test_api_flow.py
允许修改目录：TASKS.md
禁止修改目录：docs/spec/**、backend/**、tools/**
交付分支：agent/local/trae/MVP-08-verify/a1
背景：MVP-08 在 TRAE 验收前经 3de2115 合入 main（第三次「先合后验」，
      验收结论与违规留档是本任务的交付物）。GATE-MERGE-CHECK-01 以本任务
      结论转 done 为全绿启用前提。
验收方法（同 MVP-04-verify）：不采信卡片「有断言」，逐条 test_id 找
      「代码行 + 测试断言」双证据，并实跑门禁。
完成标准：
  1. DOC-TRACE-001：API 链路与 R10 §7/§21/§11 逐点对应，错误码与 prep 表一致；
  2. MVP-API-001：建群→发方案→批准→领取→心跳→提交→审查 全链路可仅经 API 完成，
     测试有机械断言；卡片自陈缺口（两处迁移用服务层驱动）核实不伪造 receipt；
  3. 违规留档（三段式）；MVP-08 任务表/认领表/卡片状态据结论更新。
test_id：DOC-TRACE-001、MVP-API-001
测试命令：cd backend; ..\.venv\Scripts\python.exe -m pytest -q
当前状态：✅ done
完成时间：2026-10-05 21:00
commit：087eb93（验收证据提交；本行为随附回填提交）
验证结果：
  结论：✅ 通过（附遗留清单 4 项，无 P1）。逐条双证据（代码行 + 机械断言）
  与门禁实跑记录已回填于本文件 MVP-08 卡末「验收意见」，此处只摘门禁数字：
  - docstate 90/0；pytest 全量 111 项退出码 0（MVP-08 新增 22：
    test_api_flow 4 + test_api_errors 10 + test_api_events_messages 8）；
  - ruff check 全过、ruff format 40 files already formatted；
  - docs/prep/MVP-08/check_contract.py：FAILURES 0；
  - docs/prep/MVP-08/mutation_test.py：caught 10/10（契约守卫非空转）；
  - 真实 app.main:app 冒烟：建项目/建群/发方案/批准/领取/事件/消息全 200，
    响应零 access-control-* 头，非 loopback Host 头 400 auth.host_header_rejected。
  遗留（详见 MVP-08 卡）：
  1. 错误码越封闭表：claim.concurrent_claim、request.conflict（prep §0.1）；
  2. HTTP 状态不符：review.self_review_forbidden 实现 409/表定 403，
     request.invalid 服务层发 422/表定 400；
  3. 消息 per-group seq 非全局（已由 message-global-seq 关闭）、
     refs 字段接收即丢（已由 message-refs-persist 关闭，迁移 004）；
  4. 两处脚手架迁移待 MVP-05/06 换成真实 HTTP 路径后 MVP-API-001 转 ✅。
  违规留档：第三次「先合 main 后验收」（3de2115）三段式记录见上文。
  MVP-08 任务表行、认领行 #17、卡片标题/状态已随本验收更新为 done。
~~~
