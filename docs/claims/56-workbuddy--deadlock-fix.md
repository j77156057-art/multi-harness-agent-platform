# 认领：deadlock-fix

| 字段 | 值 |
|---|---|
| task_id | `deadlock-fix` |
| Harness | WorkBuddy |
| 认领时间 | 2026-10-07 02:10 |
| 分支 | `agent/workbuddy/deadlock-fix/a1`（合并用）、`agent/workbuddy/deadlock-claim/a1`（本登记用） |
| 状态 | 🟢 done |
| 工作区 | `.worktrees/wbdx`（修复）、`.worktrees/wbdl`（登记） |

任务表对应行：`TASKS.md` → 「任务表」，搜 `deadlock-fix`。

## 任务

修掉 `mergeguard` × `discipline` 的**合并死锁**，并补上我自己的登记违规。

## 现象与根因

**现象**：mergeguard 合并分支时用子进程跑 discipline 当门禁，discipline 的
rootworktree 组报「无法导入 mergeguard 以核对合并锁」→ 判红 → mergeguard
拒绝提交 merge commit → `.git/MERGE_HEAD` 留存 → 下一轮被自己的互斥锁检查
挡住。**永久死锁**，本轮在合 `claim-denum-coordinator-review` 时实测复现。

**根因**：`sys.path[0]` 语义。`python tools/discipline/discipline.py` 脚本方式
运行时，`sys.path[0]` 是**脚本所在目录**（`tools/discipline`），不是 cwd
（仓库根）。于是 `from tools.mergeguard import guard` 必然失败。

**为什么所有既有测试都测不到**：pytest 下 `sys.path[0]` 是 cwd（仓库根），
同一段代码通过。这个 bug 只在「脚本方式运行」这条真实路径上出现，而测试全
部走 pytest。所以必须用**子进程跑脚本**来测——`python -c` 也不行，`-c` 的
`sys.path[0]` 是 cwd，会得到假绿。

## 修法

新增 `_ensure_repo_root_on_path()`：按 `__file__` 上溯两级推出仓库根，幂等地
插到 `sys.path` 最前面。导入前调用。

## 完成标准

1. `test_rootworktree_deadlock.py` 5 条回归测试全过（含 1 条子进程跑脚本的）
2. `discipline` 46 项全绿
3. `pytest tools/discipline` 全过
4. 用 mergeguard 实合并一次，验证不再死锁

## 本任务的两条自罚记录

| 违规 | 原因 | 处置 |
|---|---|---|
| 合并前未登记认领 | mergeguard 合并成功后才被 `registry` 组抓到「未登记=['agent/workbuddy/deadlock-fix/a1']」 | 本文件补登记 |
| 在根工作区写了 2 个文件 | H-8（根目录只用于合并与跑门禁），门禁 `[rootworktree]` 当场判红 | 已把改动搬进 worktree，根目录恢复干净 |

mergeguard 合并成功后**没有释放 `.git/merge-lock`**（持锁 pid 已退出），
是它的第三个 bug，未修，见 `docs/tasks/notes/deadlock-fix.md`。