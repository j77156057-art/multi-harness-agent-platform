# 认领：chain-template-closeout

<!-- 本文件由 WorkBuddy 创建。序号 45，接在 44-claude-code--dup-incident 之后。 -->

| 字段 | 值 |
|---|---|
| task_id | `chain-template-closeout` |
| Harness | WorkBuddy |
| 认领时间 | 2026-10-06 05:15 |
| 分支 | `agent/local/workbuddy/root-wip-chain-template` |
| 状态 | 🟢 doing |
| 工作区 | `.worktrees/workbuddy/`（**不在仓库根目录**） |
| 起点 commit | `fcfd303`（从根目录抢救的 WIP） |

任务表对应行：`TASKS.md` → 「任务表」章节，搜 `chain-template-closeout`。

## 要做什么

WIP 分支里的 chain-template 优化**已被实测证明是负收益**，需要收尾而不是提交：

| | 耗时 | 结果 |
|---|---|---|
| 基线 run1 | 479s | 17 passed |
| 基线 run2 | 525s | 17 passed |
| 优化后 | **612s** | 17 passed |

**结论：局部快（复制 0.09s vs 建骨架 98.57s）但整体更慢** ——
每个副本要在独立 `canonical.git` 上跑 `git worktree add`，副作用抵消了收益。

## 收尾清单

- [ ] 修正 `test_verify.py` 里"约 90 秒"的注释 —— 干净环境的实测是 **37 秒**，
      90秒是在 7 进程并发环境下测的，不可比
- [ ] 决定去留：若确认负收益则把 `chain_template.py` 与 `build_chain` 改动一并撤掉，
      在 TASKS.md 登记「已做已测已否决」；若要保留则必须补一组能让 worktree 环节
      变快的改动，否则留着就是负优化
- [ ] 无论去留，都要写清cProfile 的真实定位：瓶颈是 `sqlite3.Connection.close()`
      的 WAL checkpoint（30.1s x 3）与 `ensure_wal`（30.1s），**不是** git、
      也不是 SQL 解析（`apply_migrations` 只 0.02s）
- [ ] 门禁四道全绿后再提交

## 已知要避开的坑（今天的教训）

1. **基线与优化必须同环境同时间窗测**。我先在 7 进程环境测基线、后在 0 进程环境测
   优化，两个数字不可比，后来重测基线才拿到真值。
2. **`exit=0` 不等于通过**。中途有一次「1 小时 48 分 / exit=0」，实际输出只有 2 个点，
   17 个测试只跑了 2 个，SIGTERM 被吞。判结果要看测试点数。
3. **局部加速不等于整体加速**。测「复制」快就下手，没测下游副作用。

## 变更记录

- 2026-10-06 05:15：认领。WIP 从根目录抢救到 `fcfd303`，现从
  `.worktrees/workbuddy/` 继续收尾。
