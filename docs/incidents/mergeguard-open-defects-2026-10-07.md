# mergeguard 未修缺陷清单（2026-10-07 WorkBuddy 实测）

四轮合并实测下来，mergeguard 有 **4 个缺陷**，其中 **2 个仍在**且互相放大成死锁。
本文只报事实与证据，不含修法建议——修法归 mergeguard owner。

## 汇总

| # | 缺陷 | 状态 | 是否致死 | 证据 |
|---|---|---|---|---|
| 1 | commit 成功却判失败 | 已修（OpenCode `a666d23`） | 否 | — |
| 2 | 遇冲突直接退出，不 stage 解决结果、不报「有冲突待解」 | 未修 | 是（的一部分） | 见下2 |
| 3 | 合并成功后不释放 `.git/merge-lock` | 未修，**间歇性** | 是（的一部分） | 见下 3 |
| 4 | 冲突路径不写 `merge-pending.json` | 未修，**必现** | 是（的一部分） | 见下 4 |

**缺陷 3 + 缺陷 4 = 完整死锁**：4 让入口不敢接管自己的残留，3 让残留锁挡满
900 秒 TTL。就算等满 900 秒能接管，中间也已经赔掉 15 分钟；而只要再有一次
冲突，流程原地复现。

---

## 缺陷 2：遇冲突直接退出，现场既不 stage 也不标记

**现象**：合并冲突后 mergeguard 打印冲突提示、`return 4`，但：
- 解决结果不 stage（索引停在 UU态）；
- 不写任何归属记录；
- 输出的指引是「解决后跑 ./scripts.sh check，全绿再 git commit」——
  这条路要求人自己记得回来提交。

**本轮实证**：合`claim-denum-coordinator-review`（`90ef62c`）时撞上
`TASKS.md` 冲突，工具退出后留下 `.git/MERGE_HEAD`，下一轮运行被自己的
互斥锁检查挡住。我手工解冲突并提交（`2a16119` + `82a3a4a`）才脱困。

**为什么这条本身就会致死**：它不留任何「我还需要回来提交」的机器可读状态。
唯一的线索是 `.git/MERGE_HEAD` 的存在——而那恰恰是缺陷 4 用来判断
「别人的合并」的依据。

---

## 缺陷 3：合并成功后不释放锁（间歇性）

**代码**：`tools/mergeguard/cli.py:705-707` 有 `finally: guard.release(REPO_ROOT)`，
理论上必然执行。

**实测形态**：合并**成功落地**（main = `9071fb1`），但 `.git/merge-lock/`
仍在，`owner.json` 里 pid 11488 已退出。下一次任何 `mergeguard merge` 被挡：

```
已有 agent 持有合并锁，本次不合并：
  持有者 : deadlock-fix（分支 agent/workbuddy/deadlock-fix/a1）
  pid    : 11488（已退出）
```

**间歇性证据**：同一轮里第二次合并（`deadlock-claim`）就正常打印了
「已释放合并锁。」所以不是必现。

**可能根因**（未验证，不改别人的代码）：

- `guard.release()`（`guard.py:249-259`）里`path.rmdir()` 抛 `OSError` 时
  直接 `return False`，而 `cli.py:706` **没有检查返回值**——失败被静默吞掉。
- Windows 上目录被占用（IDE / 文件索引器 / git 自动维护短暂持有）会让
  `rmdir` 失败。这与「间歇性」的形态吻合。
- 缺陷 2 也可能是同一条路径的另一个表现：冲突时 `return 4` 在 `try` 块内，
  `finally` 仍会执行；但若冲突路径与成功路径的清理时机不同，就会留下差异。

**代价**：`DEFAULT_TTL_SECONDS = 900`，且 `is_stale()`（`guard.py:188`）要求
**「pid 已死」AND「超 TTL」** 同时成立。所以一个残留锁要挡 15 分钟。
本轮实测等过 904 秒。

---

## 缺陷 4：冲突路径不写归属记录（必现）

**代码顺序**（`tools/mergeguard/cli.py`）：

```
590  if not resuming:
592      proc, expected_head = _merge_with_retry(branch)
605      if proc.returncode != 0:
607          if unmerged:
608              print("合并冲突，已保留现场**不要 git merge --abort**")
625              return 4                                    ← 冲突在这里返回
626      # 记下「待提交合并」的归属，供门禁红之后续跑
628      _write_pending(branch, task_id, expected_head)      ← 永远到不了
```

`_write_pending()` 在**冲突分支之后**，所以**只要合并冲突，它就不会被调用**。

**后果**：下一次进��的 `_classify_pending_merge()`（`cli.py:484-488`）看到
「`pending is None` 且 `MERGE_HEAD` 存在」，按fail-closed 判成别人的合并：

```
拒绝：已存在进行中的合并（MERGE_HEAD 无本工具的归属记录，
可能不是本工具发起）：D:\study\tools\agent\new agent\.git\MERGE_HEAD
```

**明明是它自己留下的现场，它自己不敢认。**

**本轮实证**：合 `claim-denum-review` 冲突后立即复现。归属是靠三方交叉
验证证明的：

| 证据 | 值 |
|---|---|
| `.git/MERGE_HEAD` | `b7ba623495e5571685f39d1588d1df38549b8fe2` |
| 分支 tip `agent/workbuddy/claim-denum-review/a1` | `b7ba623495e5571685f39d1588d1df38549b8fe2` |
| 锁 `owner.json` 的 task_id / pid | `claim-denum-review` / 22348（已退出） |

三条一致即可认定「本工具本任务的残留」。**脱困方式**：手工调
`_write_pending(branch, task_id, merge_head)` 补上归属记录，之后入口就能
正常续跑（它会打印「检测到本工具上次遗留的待提交合并——跳过合并步骤，
直接续跑」）。

**注意**：`cli.py:496-500` 还有一道「记录与实际 MERGE_HEAD 不符」的检查。
补记录时 `merge_head` 必须填**实际**的 MERGE_HEAD 值（本次即分支 tip），
不能想当然填别的 commit。

---

## 建议 mergeguard owner 的修复优先级

1. **缺陷 4**（必现、直接致死）：把 `_write_pending()` 移到冲突分支**之前**，
   或者在冲突分支里也写。归属记录的成本远低于事后无法自证。
2. **缺陷 3**：`cli.py:706` 检查 `release()` 的返回值，失败就打 stderr 而不是
   静默丢掉。至少让人看得见「锁没释放，要等 900 秒」。
3. **缺陷 2**：冲突时 stage 已解决的部分（如果能安全做到），或在输出里给出
   与缺陷 4 一致的机器可读恢复指引。

## 关联任务

-任务卡：`docs/tasks/notes/deadlock-fix.md`（含完整因果链与单变量对照实验）
- 死锁修复（另一个 bug，本轮已修并合入 main）：`_ensure_repo_root_on_path()`，
  见 `docs/claims/workbuddy--deadlock-fix.md`

## 一条方法论备注

这四个 bug 有一个共同形状：**失败路径比成功路径少做了 bookkeeping**。
- 冲突路径不写归属记录（缺陷 4）
- 冲突路径不 stage、不标记待提交（缺陷 2）
- 释放失败被静默吞掉（缺陷 3）

成功路径上的每一步都有对应的收尾，失败路径上普遍没有。这与本仓库
discipline 的 fail-closed 原则其实是一致的（读不到就判红），
**但 mergeguard 缺的是另一半：失败时必须留下可恢复的机器可读状态**，
否则 fail-closed 就变成「永久锁死」而不是「安全停下」。