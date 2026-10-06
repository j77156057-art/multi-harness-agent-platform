### 违规记录：claim-nullable 第三次缺任务卡 + 认领序号撞号（d5769e2）

**事实**：WorkBuddy 的 claim-nullable（R10-fix-11 裁定，d5769e2，直提 main
无合并提交）在任务表与认领表标 done，但**第三次**没有任务卡（前两次：
R10-fix-10、fix-flaky-init）。此外认领表序号与 R10-fix-10-card **撞号 14**
——discipline 只查「卡是否存在」，不查序号唯一性，撞号没有被任何门禁发现，
由 TRAE 在 MVP-04-verify 人工核对时发现。自 d5769e2 起 discipline
check_cards 持续 1 项失败。

**已采取的纠正**：TRAE 以 claim-nullable-card（本分支）登记先行、依 d5769e2
与 R10 §38.9 补录任务卡；序号按认领时间重排（MVP-08 17、claim-nullable 18、
MVP-04-verify 19、补卡 20），补卡后 discipline 恢复 0 失败。

**系统性根因（三次同类，结论升级）**：缺卡三次全部出自同一 Harness 的
「直提 main 小修复」路径（32f4e45、9d3d3ef 搭乘合并、d5769e2 直提），
共同特征是**绕过任务分支 + 无合并者复核**。这已不是个人疏忽，而是流程缺口：
1. 既有门禁只做快照自洽（缺卡会红），但 main 允许带着红灯继续前进，
   红灯没有阻断后果；
2. 认领序号无机械唯一性检查，撞号静默存在。
**纠正措施**：GATE-MERGE-CHECK-01 除「已合入任务必须 done 且证据完整」外，
顺带加一条认领序号唯一/连续断言；在该任务上线前，直提 main 的修复同样
必须先有任务卡——TRAE 补卡只是兜底，不能成为常态依赖。

### claim-nullable：R10-fix-11 裁定 task_claims 两列可空（✅ done，任务卡事后补录）

~~~text
任务编号：claim-nullable
规范裁定号：R10-fix-11
负责人 Harness：WorkBuddy
开始时间：2026-10-05 19:30
完成时间：2026-10-05 19:39（d5769e2）
依赖任务：MVP-04（裁定其任务卡自陈的 schema 冲突阻塞项）
真相源引用：R10 §11（claim_deferred_resource 不写 owner/attempt/Lease；批量每项
            必留 task_claims 行）、§8（task_claims 定义）、§38.9（本裁定完整记录）、
            提交 d5769e2
允许修改目录：docs/spec/0.3-R10.md（§38.9 裁定记录）、backend/migrations/**、
            backend/app/claim/**、backend/tests/**、TASKS.md
交付分支：agent/workbuddy/fix-claim-nullable（d5769e2 直接进入 main）
背景：MVP-04 卡「已知问题」提出规范冲突——§11:511、§11:617 与 §8 NOT NULL
      三处不能同时满足；Qoder 用空串哨兵 NO_ATTEMPT="" 绕过，属设计债。
裁定结论：改 schema，不改 R10、不继续用哨兵。§11 两条规则都合理，冲突在 schema；
          列可空后「未领取」与「已领取」由数据层区分，不靠应用层约定。
完成内容：
  1. 新增 002_task_claims_nullable.sql：SQLite 不支持 ALTER COLUMN，走建新表 →
     NULLIF(col,'') 回填历史空串 → 换名 → 重建 UNIQUE(claim_request_id, task_id)
     与外键；
  2. NO_ATTEMPT 由 "" 改为 None；
  3. test_claim 断言 == "" 改 is None 并补 owner is None；
  4. test_database_events 两处硬编码迁移数量改为按 .sql 文件数断言（防再漂移）；
  5. 兼容性：回放走 LEFT JOIN task_attempts，本就容忍 NULL。
test_id：CORE-CLAIM-002、CORE-RECOVER-001
测试命令：cd backend; python -m pytest -q
当前状态：done（R10-fix-11 裁定）
commit：d5769e2（随 main 发布，无独立合并提交）
验证结果：
  - pytest 85 passed（MVP-04 新增 31 项），全量连跑 10/10；
  - docstate 90/90、ruff check + format 全绿；
  - MVP-04-verify 机械复核：无哨兵残留，回放/失败项路径与 NULL 语义一致。
  - 证据来源：d5769e2 提交说明，TRAE 在 MVP-04-verify 复验一致。
补录说明：
  - 本卡由 TRAE 于 2026-10-05 20:01 依 d5769e2 与 R10 §38.9 事后补录：
    WorkBuddy 直提 main 时再次遗漏任务卡（第三次同类缺卡），且认领表序号
    与 R10-fix-10-card 撞号 14，见违规记录区。
~~~
