### MVP-08-prep：REST API 契约草案、错误码表、幂等响应约定（✅ done）

~~~text
任务编号：MVP-08-prep
负责人 Harness：Qoder
开始时间：2026-10-05 09:16
依赖任务：无（prep 的定义就是不依赖 schema，见清单 prep 表）
工作区：.worktrees/qoder/
真相源引用：R10 §7（Adapter 接口）、§21（Planner/Review/Git 动词）、§8（canonical schema，
            含 R10-fix-09 对 claim_requests / task_claims 的键修正）、§11（Claim 与幂等，
            含两层幂等职责划分）、§12（命令幂等键）、§13（Lease/fencing）、
            §15（事件与 seq）、§19（群聊与 read_context）、§26（检查 ID）、
            §37.7（事件 allowlist，88 项）
允许修改目录：docs/prep/MVP-08/**（本工作区内；prep 产出不进主分支）
禁止修改目录：docs/spec/0.3-R10.md、docs/spec/mvp-task-checklist.md、backend/**、
            tools/docstate/**、docs/traceability.md（属 graph-traceability）
交付分支：agent/local/qoder/MVP-08-prep/a1
完成标准：
  1. OpenAPI 3.1 契约草案覆盖 R10 §7 全部 16 个动词与 §21 全部 6 个动词，
     /adapter/* 的 path 段拼写与 §7 原文逐字一致（零转换表）
  2. 统一错误码表：每个 code 标注 HTTP、触发条件、R10 引用、写入事件名、test_id；
     写入的事件名必须在 §37.7 的 88 项 allowlist 内
  3. 幂等响应约定：§12 的 7 类幂等键、双层 Claim 幂等（请求级 first_result_json /
     per-task task_claims，R10-fix-09 口径）、payload_hash 算法、重放响应格式
  4. check_contract.py 对仓库 docs/spec/0.3-R10.md 跑出 0 失败
  5. mutation_test.py 对每条断言做变异，全部被捕获（证明断言非空跑）
test_id：DOC-TRACE-001、MVP-API-001
测试命令：python docs/prep/MVP-08/check_contract.py && python docs/prep/MVP-08/mutation_test.py
当前状态：done
完成时间：2026-10-05 20:18
commit：888afb6（初版落地，merge 8fdce1a）+ a6d0350（按 R10-fix-10 复验，merge 33bde2b）
验证结果：
  以下两个脚本于 2026-10-05 20:18 对当时 main 的 R10（含 R10-fix-09/10/11 三轮修订）**重新跑过**：
  check_contract.py : 10 组断言，FAILURES 0（exit 0）
  mutation_test.py  : caught 10/10（exit 0）
  结卡时重跑的理由：本卡第 4 条完成标准是「对仓库 R10 跑出 0 失败」，而 R10 在本卡完成
  之后又被修过 —— 拿当时的绿灯结卡就是在用陈旧证据。
已知问题：
  - prep 产出不进主分支；MVP-08 真正开工时必须重新验证，不能直接当结论用
  - 与 graph-traceability 的边界：本任务不建 docs/traceability.md，那是 Claude 的交付物
  - R10-fix-09 之前写就的版本把 per-task 幂等落点放在 claim_requests，已在本次修正
~~~
