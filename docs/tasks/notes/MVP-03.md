### 违规记录：MVP-03 先合 main 后验收（db9bc19）

**事实**：MVP-03（Codex，分支 `agent/local/codex/MVP-03/a1`）在**TRAE 验收完成前**
就经 `db9bc19` 合入 main。TRAE 验收结论为 Conditional Fail，机械对拍发现三个 P1：

- ❌ 代码迁移表与 R10 §10 不一致：缺 25 条展开边、多 11 条非法边；
- ❌ Plan validator 未拒绝重复依赖边，且重复边会造成 Kahn 假环误报；
- ❌ 没有代码表与规范表的机械对拍，缺陷无法被门禁发现。

即：合入 main 的代码未满足"验收通过才可合入"的 Gate 语义，main 一度带着
已知 P1 缺陷前进。

**已采取的纠正**：

1. TRAE 在 `agent/local/trae/MVP-03-amend/a1` 完成整改（MVP-03-amend）：
   迁移表与 R10 §10 的 138 个展开三元组零差异、validator 拒绝重复边、
   新增 `backend/tests/test_transition_mirror.py` 机械对拍防再次漂移；
2. 整改按"登记先于开工"执行（91e350e 先于代码提交 b9f140e），
   完成后回填任务卡证据（5b91364），再经本记录同批合入 main；
3. 本记录作为既成事实的流程偏差留档。

**根因**：验收被放在合并之后，Gate 没有卡住"未验收"的合入。**合并的前置条件
必须是验收通过（含机械对拍），不能用"先合、验收问题再追 amend"替代。**

### MVP-03：状态机与 Plan 校验（✅ done，凭 amend 证据关单）

~~~text
任务编号：MVP-03
负责人 Harness：Codex
开始时间：2026-10-05 09:07
完成时间：2026-10-05 09:21
依赖任务：MVP-02（✅ 已验收）
工作区：.worktrees/codex/
真相源引用：R10 §10（状态机与迁移表）、§37.7（事件 allowlist）、
            §8（tasks 与 task_dependencies schema）、§9（Plan/任务契约）、§26（检查 ID）
允许修改目录：backend/app/state/** backend/app/plan/** backend/tests/**
禁止修改目录：docs/spec/**、tools/docstate/**、backend/app/db/**、backend/app/events/**
交付分支：agent/local/codex/MVP-03/a1
完成标准：
  1. 已实现状态只能通过 R10 allowlist 中的迁移事件变更；非法迁移被拒绝 ✅
  2. 任务依赖 DAG 拒绝环、孤立引用和重复边 ✅
  3. Plan 校验拒绝未注册能力、非法依赖、不可满足产出契约和超规模输入 ✅
  4. 投影重建可从 task_events 产生同一状态结果，不直接改数据库状态字段 ✅
test_id：DOC-STATE-001、DOC-PLAN-001
测试命令：./scripts.sh check
当前状态：✅ done（初验 Conditional Fail，P1 由 MVP-03-amend 闭环）
commit：9b4b663
验证结果：
  docstate   : 88 项检查，0 项失败
  discipline : 17 项检查，0 项失败
  ruff check : All checks passed
  ruff format: 18 files already formatted
  pytest     : 26 passed in 1.53s
  关单复核（TRAE 2026-10-05 20:47，GATE-MERGE-CHECK-01 启用批次）：
  - 三个 P1（迁移表缺 25 边/多 11 边、重复边导致 Kahn 假环）的修复有
    test_transition_mirror.py 机械对拍：代码迁移表与 R10 §10 全部 138 个
    state-event-next 三元组零差异；重复边拒绝有专门用例（见 MVP-03-amend 卡）；
  - 修复后的代码构成 MVP-04 的构建基线，并通过 MVP-04 正式验收
    （MVP-04-verify 静态核查 + verify-mvp-04 注入验证 3/3）间接覆盖；
  - 先合后验流程违规已在本文件「违规记录：MVP-03 先合 main 后验收（db9bc19）」留档。
已知问题：
  - scripts.sh 在当前工作区按原始 CRLF 直接由 bash 执行会出现 pipefail 换行解析问题；本次使用等价的去 CRLF 管道执行，四道门禁结果全绿。
  - 状态机是纯投影层，尚未接入 REST/API 和数据库写入；由后续 MVP-04/MVP-08 负责。
~~~
