### ADMIN-03：管理员运行时（prep）

~~~text
任务编号：ADMIN-03
负责人 Harness：WorkBuddy / workbuddy-admin03-a1
开始时间：2026-10-09T15:35+08:00（本人领取；正式实施未开始）
工作区：.worktrees/workbuddy/
依赖任务：ADMIN-01（契约未冻结）
真相源引用：AGENTS.md §1/§3/§5b/§6；docs/dev-convention.md H-4/H-5/H-8；R10 §38
允许修改目录：backend/app/coordinator/**、backend/tests/test_admin_coordinator*.py；隔离 prep 仅本工作区 docs/prep/ADMIN-03/**
禁止修改目录：公共 routes/auth/schema/config、messaging、harness_relay、desktop_broker、rag-agent 原件、其他 Harness 目录
交付分支：agent/admin/workbuddy/ADMIN-03/a1
test_id：MVP-PROCESS-001、DOC-TRACE-001（目前仅登记与追溯；实施验收绑定待冻结）
当前状态：prep
完成标准：冻结契约发布并重验五项全部通过后才可实施；实施交付等待独立复核，不凭本登记关单
已知问题：ADMIN-01 未冻结，管理员功能验收编号/路径尚未正式绑定；真实模型未验证
~~~

拟议实施用例（必须在契约冻结时正式绑定，当前不声称有测试）：
ADM-RUN-001 结构化输出；ADM-RUN-002 固定 provider/model；ADM-RUN-003
预算/截止/取消；ADM-RUN-004 原需求与版本追溯；ADM-RUN-005 inline MCP
不扩权；ADM-AUTH-003 禁止管理员写产品代码；ADM-AUTH-004 旧委托 epoch 拒绝。

冻结后由本人按工作区 README 的 re-validation 顺序重验：

1. 重新核对来源 hash；变更则重做差异说明。
2. 核对 coordinator 目录及在途分支的占用，不能以目录不存在替代 H-8。
3. 核对 main 中 claim 属于本实例，分支/worktree 与本人回执一致。
4. 读取发布的冻结 DTO/工具 IO/投递状态/epoch 契约，按其重写准备材料。
5. 更新自己的分支至合适基线，完整门禁全绿，再串行登记实施状态。

下一交付：contracts/budget/idempotency/registry/schema/loop/run_state 模块及
专属测试；真实模型与 mock 证据分开报告，先进入待审，不直接宣布完成。
