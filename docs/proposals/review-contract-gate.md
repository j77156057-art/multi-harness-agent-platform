# 提案：评审契约守门（review-contract-gate）

状态：**已裁决并实施**。仓库主人裁决：A+B 都做；缺少 `check` 的验收条款
拒绝发布。A 已在 `backend/app/tasks/service.py` 实施；B 已由既有
`PlanValidator` 实施并经测试确认，无需重复改动。

## 问题

P3（assistant-react-loop-p3）给审核人垫好了素材包：验收条款、diff、
回执一次取证。但守门侧有两个现状缺口：

1. **submit_review 不看条款**：findings 是自由文本，审查不需要逐条对到
   验收契约——"approve"可以在一条都没写证据的情况下成立；
2. 缺少或非法 `check` 的条款必须在发布计划时拒绝。

## 分工（顺着"if/else 归代码"的尺子）

逐条覆盖校验是正折则的 if/else——现在是平台代码欠的账，不该靠助手
念叨来补。

## 决定与实现

- **A. 审核提交守门**：`approve` findings 必须以 `contract_id` 覆盖任务
  绑定 plan snapshot 中的每个 acceptance id。缺失报 `review.contract_missing`，
  引用未知 id 报 `review.contract_unknown`。显式 finding 可标
  `no_evidence: true` 并说明仍需人工查验；`request_changes` 不要求覆盖。
- **B. 计划发布守门**：`PlanValidator` 已拒绝缺失或非法的 acceptance `check`
  （只接受 `cmd` / `human`）；计划创建和修订复用该校验。
- 不新增表、路由、事件、权限键或检查 ID。

## 验收

- approve 条款缺失和未知 id 均被机械拒绝；显式 `no_evidence` 可覆盖；
  request_changes 不受覆盖规则限制。
- 无验收条款的历史任务保持兼容。
- 实施 commit 与测试结果回填至任务卡和认领记录。

## 成本/影响

不加表、不加路由；A 只校验既有 findings JSON，B 复用既有计划校验器。
所有新增测试为 scripted，不烧真额度。
