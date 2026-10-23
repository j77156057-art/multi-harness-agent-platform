# 提案：评审契约守门（review-contract-gate）

状态：**提案，待仓库主人复核**；未开工、未写代码。落点
`backend/app/tasks/service.py` 是共享在途文件（FORMAL-MULTI-HARNESS-02
未收敛），按共享文件纪律单开提案，不在 P3 卡里抢跑。

## 问题

P3（assistant-react-loop-p3）给审核人垫好了素材包：验收条款、diff、
回执一次取证。但守门侧有两个现状缺口：

1. **submit_review 不看条款**：findings 是自由文本，审查不需要逐条对到
   验收契约——"approve"可以在一条都没写证据的情况下成立；
2. **没有 check 的条款被静默收下**：plan 快照里 `{"id": "T-3", ...}` 不写
   `check` 也能发布（素材包里已如实数出来，但发布面本身不拦）。

## 分工（顺着"if/else 归代码"的尺子）

逐条覆盖校验是正折则的 if/else——现在是平台代码欠的账，不该靠助手
念叨来补。

## 候选修法（二选一或组合，待裁决）

- **A. 提交面守门**：`submit_review` 的 findings 增加 `contract_id`
  字段；`approve` 时校验任务验收契约逐条有对应 finding（或显式
  `no_evidence,还是要人看` 注记），缺了给 `review.contract_missing`
  Conflict。request_changes 不拦（提问题自由）。
- **B. 发布面守门**：`_plan_snapshot` 正常化验收契约时，无 `check` 的
  条款要么拒绝、要么强制默认 `{"check": "human"}` ＋显式注记
  "由人查验"（现在实际是**静默认定**，读者不知这是默认）。

## 待裁决的问题（给仓库主人）

1. 守门落点：A、B 都做，还是先做一个？
2. 无 check 条款：拒绝、还是默 human＋显式注记？默认值是什么？
3. 新检查 ID（如 `MVP-REVIEW-002`）规范条目待复审后补挂真相源。
4. `tasks/service.py` 共享在途（FORMAL-MULTI-HARNESS-02）；
   实施窗口等这块收敛后单开任务卡再动。

## 成本/影响

不加表、不加路由；A 只在既有 findings 的校验 + 一个错误码；B 只改
一个函数的正则化分支。scripted 验收 3–4 条，全不烧真额度。
