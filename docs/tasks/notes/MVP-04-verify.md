### MVP-04-verify：MVP-04 验收（✅ done）

~~~text
任务编号：MVP-04-verify
负责人 Harness：TRAE
开始时间：2026-10-05 19:47
依赖任务：MVP-04（8c0233a，已随 e656058 合入 main）、claim-nullable（d5769e2，
          R10-fix-11 对 MVP-04 自陈阻塞项的裁定修复，验收基线须包含）
工作区：.worktrees/trae/
真相源引用：R10 §7 §8 §10 §11 §12 §13 §26 §28；MVP-04 任务卡（本文件）；
            提交 8c0233a（MVP-04 交付）、d5769e2（R10-fix-11）
允许修改目录：TASKS.md（验收结论、违规登记）；其余目录只读核查
禁止修改目录：docs/spec/**、backend/**、tools/**（验收者不改被验代码）
交付分支：agent/local/trae/MVP-04-verify/a1
验收方法：不信测试数，逐条对照规范机械核查：
  1. CORE-CLAIM-001：双连接并发 Claim 同任务仅一个成功；BEGIN IMMEDIATE 事务顺序、
     条件 UPDATE RowsAffected==1、guard（attempt_no < max_attempts OR
     budget_override_by IS NOT NULL）与 R10 §11 逐字一致；
  2. CORE-CLAIM-002：两层幂等——claim_request_id 整体回放 +
     UNIQUE(claim_request_id, task_id) per-task；含失败项每个任务项都有 task_claims
     行；资源预占失败只进 queued、不写 owner/attempt/Lease；
  3. CORE-LEASE-001：Lease 创建/续期/过期回收路径；
  4. CORE-FENCE-001：fencing epoch 单调，旧 epoch heartbeat/写入被拒；
  5. R10-fix-11 后复核：attempt_id/owner 已改 nullable，Qoder 卡中的 NO_ATTEMPT
     空串哨兵是否仍残留、是否与新 schema 矛盾；
  6. 核对 Qoder 卡「未实现」三项（能力/质量门控、path_conflicts、
     dependencies_not_merged）确属后续 MVP 而非 §26 验收项。
完成标准：
  1. 四条 test_id 逐条给「通过 / 缺陷（开 amend）」结论并附代码行证据；
  2. 验收基线为当前 main（含 d5769e2），四道门禁结论写清；
  3. 「review 状态合入 main」按违规记录区三段式留档（第二次同模式）；
  4. 通过则 MVP-04 卡转 ✅ 已验收，不通过则列明 amend 清单。
test_id：CORE-CLAIM-001、CORE-CLAIM-002、CORE-LEASE-001、CORE-FENCE-001
测试命令：cd backend; ..\.venv\Scripts\python.exe -m pytest -q
当前状态：done
完成时间：2026-10-05 19:55
commit：6bb4271（登记先于开工）、关卡提交随本提交闭合（见 git log）
验证结果：
  - 验收结论：✅ 通过（附遗留清单），四条 test_id 逐条证据见 MVP-04 卡末；
  - docstate：90 项 0 失败；ruff check/format：全过；
  - pytest：85 passed；test_claim_concurrency.py 连跑 10/10；
  - discipline：24 项中 1 项失败为既有红灯（缺卡=['claim-nullable']），
    非本任务引入，未越权代修。
后续事项（交用户/协调者）：
  1. claim-nullable（R10-fix-11）缺任务卡——第三次同类缺卡；另认领序号
     与 R10-fix-10-card 撞号 14，需补卡任务一并更正；
  2. GATE-MERGE-CHECK-01（discipline git 感知检查）按既定顺序开工；
  3. 遗留清单第 4 项（并发窗口/path_conflicts 排期归属）需协调者指派。
~~~
