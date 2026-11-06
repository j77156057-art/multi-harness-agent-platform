# attempt 收口 dry-run 映射（316 条）

日期：2026-11-06
对象：`data/state.db`（本机运行库，只读副本口径）
性质：**只读 dry-run**。本文只回答「每一条该归到哪、依据是什么」，**不执行任何 UPDATE**。

## 1. 口径

统计的是「任务已经不可能再用这个 attempt，但 `closed_at` 仍为空」的行：

```sql
SELECT t.status AS task_status, a.status AS attempt_status, COUNT(*) AS n
FROM task_attempts a JOIN tasks t ON t.task_id = a.task_id
WHERE a.closed_at IS NULL
  AND a.status IN ('claimed', 'in_progress', 'verifying')
  AND t.status NOT IN ('claimed', 'in_progress', 'needs_help', 'blocked',
                       'verifying', 'awaiting_human', 'plan_drift', 'impl_drift')
GROUP BY t.status, a.status;
```

结果 316 条，分布在 170 个任务上；其中 50 条的 `tasks.current_attempt_id`
仍指向它（其余指针已经移走或清空）。这是 `attempt_reconcile` sweep 每轮
报出来的同一批行（`open_attempts: 316`）。

## 2. 映射表

`close_reason` 一列是「如果回填，应该写什么」。本轮新增的 `finish_attempt`
用事件名作 reason，所以历史行也按同一套词汇归，才能与今后新数据对得上。

| # | 任务状态 | attempt 状态 | 条数 | 归到 attempt.status | close_reason | 依据 |
|---|---|---|---|---|---|---|
| 1 | merged | verifying | 40 | `merged` | `merge_success` | 合并已落地；这 40 条正是 `verify_passed` 不关 attempt 的根因 |
| 2 | merged | in_progress / claimed | 2 + 2 | `merged` | `merge_success` | 同上，只是 attempt 更早阶段就停了 |
| 3 | cancelled | verifying / claimed / in_progress | 42 / 19 / 14 | `cancelled` | `cancel` | 取消是终态，attempt 不可能再被使用 |
| 4 | abandoned | in_progress / claimed / verifying | 63 / 8 / 3 | `abandoned` | `abandon` | 放弃是 owner 阶段结束，由平台收口 |
| 5 | ready | claimed | 77 | `released` | `release` | `seq 2562` 那类：release 事件已落盘、任务回到池子，attempt 停在 claimed |
| 6 | ready | in_progress | 1 | `released` | 需看事件（`release` / `reopen` / `reopen_no_owner` / `all_children_merged`） | 四种上游都可能把任务送回 ready |
| 7 | stale | claimed / in_progress / verifying | 17 / 4 / 3 | `failed` | `lease_expire` | 租约到期判失联，这一轮已经结束 |
| 8 | escalated | claimed / verifying / in_progress | 5 / 3 / 1 | `escalated` | 需看事件（`verify_*_timeout` / `review_*` / `merge_timeout` / `stuck_threshold_reached` …） | 升级原因不同，事后要能区分 |
| 9 | awaiting_review | verifying | 8 | `released` | `verify_passed` | 提交已有结论、任务在等评审，attempt 不可能再被使用 |
| 10 | awaiting_review | claimed | 1 | `released` | **待确认** | 领取后直接进了评审，需单条看 `task_events` 才能定性 |
| 11 | approved | verifying | 1 | `released` | `verify_passed` | 同上（评审已通过） |
| 12 | changes_requested | verifying | 2 | `failed` | `verify_code_failed` | verify 判失败，返修应是新的 attempt |
| — | **合计** | | **316** | | | |

### 按「回填难度」归类

| 类别 | 条数 | 说明 |
|---|---|---|
| A. 任务状态本身就是答案 | 229 | 表内 1–4、7、8（部分）、11、12：`merged` / `cancelled` / `abandoned` / `stale` / `approved` / `changes_requested` 直接给出关闭态 |
| B. 关闭态明确、reason 要按事件细分 | 78 | 表内 5、6（ready）：归 `released`，但 `release` / `reopen` / `all_children_merged` 三种上游必须靠 `task_events` 区分 |
| C. 根因已知 | 8 | 表内 9：`verify_passed` 之后没收口，就是本轮修掉的那条路径 |
| D. 需人工单条确认 | 1 | 表内 10：`awaiting_review` + `claimed` |
| **合计** | **316** | |

## 3. 为什么不现在回填

1. **先看趋势，再决定动谁。** 新的 `attempt_reconcile` sweep 已经上线，每轮
   只读报出这个数字。规则是：观察一到两周，看它是**不动**还是**持续增长**。
   - 数字不动 → 说明漏路径已补齐，这 316 条纯粹是历史沉积，那时回填是低风险
     的数据整理；
   - 数字持续增长 → 说明**还有没被收口的路径**。这时该修的是路径（像本轮修
     `verify_passed` 那样），而不是改数据；先回填会把证据抹掉，让下一处漏点
     继续隐身。
2. **这 316 条里有 229 条是 A 类**，回填只需要状态映射；但 B 类的 `close_reason`
   依赖事件考古，D 类还要人工判断。现在做，等于把一次性判断固化成永久事实，
   而判断依据（事件、当时的 owner 行为）本身还没被复核过。
3. **回填不解决任何当前故障。** 它只让审计查询更干净；而 sweep 已经把
   "不干净"变成可见提醒。可读性收益 < 误标风险。

## 4. 真要回填时的手顺序（本轮不执行）

1. 先对 B 类按 `task_events` 建一张 `attempt_id → 决定性事件` 的映射表，
   人工抽检 10%；
2. 用 `WHERE closed_at IS NULL AND status IN (...)` 作护栏，逐批提交、每批
   前后跑一次 `attempt_reconcile` 对账；
3. 只写 `status` / `closed_at` / `close_reason`，**不动** `tasks.current_attempt_id`
   （是否清指针是另一个决定，涉及 claim/reclaim 契约）；
4. 回填脚本本身不进库，产出（前后计数）写进 `artifacts/`，结论回填本文。

## 5. 现状声明

本轮**没有执行任何 UPDATE**：316 条保持 `closed_at IS NULL`，67 行历史切片
与 8 条 `awaiting_review + verifying` 均未改动。
