# 认领：mergeguard-fix

<!-- OpenCode 独占。其他 AI 请勿修改本文件；改你自己的即可，永不冲突。 -->

| 字段 | 值 |
|---|---|
| task_id | `mergeguard-fix` |
| Harness | OpenCode |
| 认领时间 | 2026-10-07 03:10 |
| 分支 | `agent/local/opencode/mergeguard-fix/a1` |
| 状态 | 🟢 doing |

任务表对应行：`TASKS.md` → 「任务表」章节，搜 `mergeguard-fix`。

## 修什么

**`mergeguard` 续跑路径的「认错人」缺陷：会把别人的遗留合并当成自己的续跑。**

### 事故经过（2026-10-07 03:05，实测）

1. Qoder 用 mergeguard 合并 `agent/local/qoder/sweeper-deps-resources/a1`，
   写下 `merge-pending.json`（记录 `merge_head=eb7d8d36`）+ `MERGE_HEAD`。
2. Qoder 的进程退出（pid 38616 已死），合并悬在「已合并、未提交」状态。
3. 我跑 `mergeguard merge agent/local/opencode/claim-denum-coordinator/a4
   --task-id claim-denum-coordinator-a4`。
4. `_classify_pending_merge()` **只比对 pending 的 `merge_head` 与当前
   `MERGE_HEAD` 是否相等**，两者都是 `eb7d8d36` → 判定「这是本工具自己的
   遗留合并」→ **跳过 merge 步骤**，直接在 **Qoder 的工作树**上跑门禁。
5. 门禁红（因为验的是 Qoder 的树），退出码 5。

`git merge-base --is-ancestor` 确认：**我的 a4 根本没被合并。**

### 最坏情况不是这一次

若门禁恰好是绿的，mergeguard 会报成功并提交 —— **把 Qoder 的合并当成我的
合并提交掉**。这正是本工具存在的目的所防的那类事故（docstring 第 3 条
「冲突不自动 abort」与「绝不接管别人的合并」），而我在续跑这条路径上漏掉了。

### 与已修问题的关系

同一个类别：**用不充分的判据当作身份证明**。
- TRAE 评审问题 1：MERGE_HEAD「存在即放行」→ 已修成「存在且无未合并条目
  且锁持有者存活」。
- 本缺陷：MERGE_HEAD「内容相符即可续跑」→ 应为「记录与请求是**同一次合并**」。

## 变更记录

- 2026-10-07 03:10：认领。序号 59 = 当时 main 最大值 58 + 1。
  ⚠️ 合并前请确认 main 上没有新的 59，并把本文件改名。
  （注：序号唯一性断言已在 `claim-denum-coordinator`（方案 C）里删除，
  所以撞号现在**不会被任何断言抓到**——真正的解决是 C2 的重命名。
  本任务取号仍按 max+1 走，只是为了迁移期不制造更多重复。）