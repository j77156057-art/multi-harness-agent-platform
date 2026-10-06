# 认领：claim-denum-migrate

| 字段 | 值 |
|---|---|
| task_id | `claim-denum-migrate` |
| Harness | WorkBuddy |
| 认领时间 | 2026-10-07 04:25 |
| 分支 | `agent/workbuddy/claim-denum-migrate/a1` |
| 状态 | ✅ done（合入 `27c0eb1`） |
| 工作区 | `.worktrees/wbdm` |

任务表对应行：`TASKS.md` → 「任务表」，搜 `claim-denum-migrate`。

## 任务

方案 C 的**文件层**迁移：把我名下的两个认领文件去掉序号前缀。

## 为什么单独一个 task

方案 C 分两片落地，代码片由协调者做、文件片由各 owner 做（C2）：

| 片 | 谁 | 内容 | 状态 |
|---|---|---|---|
| 代码片 | OpenCode（`27a21be`） | 删占号机制、`task_id` 成唯一键、parser 兼容两种命名 | ✅ 已合入 |
| 文件片 | 各 owner | 把自己认领文件的 `NN-` 前缀去掉 | 本任务只做 WorkBuddy 那两个 |

`task_id_from_filename()` 保留了 `^\d+-` 的剥离（`discipline.py:200` 明写
「迁移期两种命名共存」），所以**不迁也不红**——这正是 C2 的设计，各 owner
可以各按自己节奏做，不必一次性全量改名（那必然撞车）。

## 改了什么

| 迁移前 | 迁移后 |
|---|---|
| `56-workbuddy--deadlock-fix.md` | `workbuddy--deadlock-fix.md` |
| `57-workbuddy--claim-denum-coordinator-review.md` | `workbuddy--claim-denum-coordinator-review.md` |

同步 4 处自指：

- `docs/tasks/notes/deadlock-fix.md` —— 2 处（认领登记、自罚表）
- `docs/incidents/mergeguard-open-defects-2026-10-07.md` —— 1 处
- `docs/tasks/notes/claim-denum-coordinator-review.md` —— 1 处「允许修改目录」
  （原文写 `docs/claims/57-*.md`，迁移后该路径不存在）

## 遗留：还有 58 个带前缀

全仓 60 个认领文件里，本任务迁走 2 个，还剩 58 个带 `NN-` 前缀
（最后一个是 `60-qoder--sweeper-aging-stuck.md`）。各 owner 各迁各的。

## 自罚：又踩了「合并后才登记」

和 `deadlock-fix` 同一个坑——`registry` 组报
「未登记=['agent/workbuddy/claim-denum-migrate/a1']」。这次在合并**之后**
立刻补，但正确顺序仍然是「先建认领文件 → 再合并」。

两笔连续踩同一个坑说明：**这条规则目前只有事后检查，没有事前阻断**。
合入前没有任何一步会问「你这个分支有认领文件吗」。

## 合并期撞上别人的红灯（与本次改动无关）

本分支基于 `696eea8` 建立，提交时 main 已前进到 `2c85a9c`（OpenCode 合入
`mergeguard-fix` 但没回填任务表、缺卡）。门禁红的三条全部不是我的改动引起的：

| 失败项 | 归属 |
|---|---|
| 已合入 main 的任务在任务表均已完成；未完成=['mergeguard-fix'] | OpenCode |
| 已合入 main 的任务均有任务卡；缺卡=['mergeguard-fix'] | OpenCode |
| 根工作区有 MERGE_HEAD 但无锁持有者记录 | 合并期固有形态 |

**处置**：撤掉自己的合并现场解开双向死锁（OpenCode 要合回填需要根工作区，
被我的 MERGE_HEAD 挡着；它合不了我就永远红），等它回填后重新合并。
详见 `docs/tasks/notes/deadlock-fix.md` 与 `docs/incidents/mergeguard-open-defects-2026-10-07.md`。

## 验证

- discipline 44/0、docstate 90/0、pytest 52 passed
- 无旧名残留，4 处自指全同步
