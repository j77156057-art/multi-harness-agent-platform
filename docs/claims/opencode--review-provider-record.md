# 认领：review-provider-record（审查行没记住提供方）

<!-- OpenCode 独占。其他 AI 请勿修改本文件；改你自己的即可，永不冲突。 -->

| 字段 | 值 |
|---|---|
| task_id | `review-provider-record` |
| Harness | OpenCode |
| 认领时间 | 2026-10-17 22:05 |
| 分支 | `agent/local/opencode/review-provider-record/a1`（尚未切出；改动挂在 codex 的 `FORMAL-MULTI-HARNESS-01/a1` 工作区，如实记录） |
| 状态 | doing：两处插入路径已修 + 3 条测试；线上真实审查复核待做 |

任务表对应行：`TASKS.md` 的「任务表」章节，见 `review-provider-record`。

## 背景：线上真审查把这一列写成了 `unknown`

管理员 harness 在正式平台做的第一次跨提供方审查：

    review_id           rv-1fb66923834e4b4eb0ec10fa1db8f209
    reviewer_harness_id office-admin
    reviewer_provider   unknown          ← 应为 qoder

`harnesses` 表里 `office-admin` 的 provider 是 `qoder`，注册面早就知道；两条写
`reviews` 的路径（`review_claim` 的 INSERT、`submit_review` 的无租约 INSERT）都把
`reviewer_provider` 写死成 `"human" if human else "unknown"`。

## 为什么这是缺陷，而不是"暂时没人读就无所谓"

1. §21 的跨提供方规则靠"两个提供方都已知"成立。审查行自己把提供方记成 `unknown`，
   事后无法从这笔审查重建"谁（哪个提供方）审了谁"，审计链断在自己这一行上。
2. 现在确实没有别的代码读这一列 —— 所以它今天不会算错结果，但它会**继续错误地
   记录历史**。等有人拿这一列做统计或做二次门禁时，坏数据已经在库里了。
3. 修法极小：同一个事务里的 `conn` 查一次 `harnesses.provider`，取不到才降级
   `unknown`（fail closed，不猜）。

## 修法

`app/tasks/service.py`：

- 新增模块级 `_reviewer_provider(conn, *, reviewer_harness_id, reviewer_kind)`：
  `human` → `"human"`；harness → `SELECT provider FROM harnesses WHERE harness_id = ?`，
  无行/空值 → `"unknown"`。用调用方的 `conn`，因为两处都在写事务里（不开第二条连接）。
- 两条 INSERT 改用它。

## 测试（都能失败）

- `test_the_review_records_the_reviewers_registry_provider`：`review_claim` 后断言
  `reviewer_provider == "anthropic"`（注册面给的），不是字面量 `unknown`。
- `test_an_unknown_harness_provider_degrades_to_unknown_not_invented`：直接断言帮助函数
  的降级分支。**注意**：`cross_provider` 策略会在 `review_claim` 之前就拒绝未注册
  reviewer，所以这条降级路径在正常流程里不可达 —— 因此测帮助函数而不是伪造一条
  不可达的 claim。
- `test_a_human_review_records_human`：人工审查仍记 `human`。

## 验证

~~~bash
cd backend
../.venv/Scripts/python.exe -B -m pytest tests/test_review_findings.py -q
../.venv/Scripts/ruff.exe check . && ../.venv/Scripts/ruff.exe format --check .
~~~

- `tests/test_review_findings.py`：9 通过（原 6 + 新 3）。
- 全量：737 用例 RC=0；docstate 98/0；discipline 44/0；ruff 全绿。
- 线上复核（真 UI 点击「领取审核」）：`rv-817cfd3f049347f7852252cec517ce42`
  的 `reviewer_provider` 记录为 `qoder`（修复前的 `rv-1fb669…` 是 `unknown`），
  说明修复在正式平台生效。

## 变更记录

- 2026-10-17：认领并修复；由 `rv-1fb66923834e4b4eb0ec10fa1db8f209` 现场发现。
