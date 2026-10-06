### claim-denum-migrate：方案 C 文件层迁移（WorkBuddy 名下 2 个认领文件）（✅ done）

~~~text
任务编号：claim-denum-migrate
负责人 Harness：WorkBuddy
开始时间：2026-10-07 04:25
工作区：.worktrees/wbdm
依赖任务：deadlock-fix
真相源引用：docs/proposals/claim-denum.md（OpenCode，2026-10-06 02:39）
            docs/proposals/claim-denum-coordinator-review.md（WorkBuddy，裁决材料）
            docs/claims/workbuddy--claim-denum-migrate.md（本任务认领文件）
允许修改目录：docs/claims/workbuddy--*.md（本人名下）、docs/tasks/notes/ 下本人卡片、docs/incidents/ 下本人报告、TASKS.md任务表
禁止修改目录：tools/**、backend/**、docs/spec/**、docs/claims/ 下他人文件
交付分支：agent/workbuddy/claim-denum-migrate/a1
test_id：MVP-PROCESS-001
当前状态：done
完成时间：2026-10-07 05:0x（文件层迁移合入 `27c0eb1`；补登记随本次提交落地）
commit：`c9425aa`（迁移本体）→ `27c0eb1`（合入 main）
验证结果：见下方「验证结果」
~~~

### 范围：只做 C2 的文件片

方案 C 分两片落地：

| 片 | 谁 | 内容 | 状态 |
|---|---|---|---|
| 代码片 | 协调者 / OpenCode（`27a21be`） | 删占号机制、`task_id` 成唯一键、parser 兼容两种命名 | ✅ 已合入 |
| 文件片 | 各 owner | 把自己认领文件的 `NN-` 前缀去掉 | **本任务只做 WorkBuddy 那 2 个** |

`task_id_from_filename()` 保留了 `^\d+-` 的剥离（`discipline.py:200` 注释明写
「迁移期两种命名共存」），所以**不迁也不红**——这是 C2 的设计意图，
各 owner 按自己节奏做，避免一次性全量改名必然撞车。

### 改了什么

| 迁移前 | 迁移后 |
|---|---|
| `56-workbuddy--deadlock-fix.md` | `workbuddy--deadlock-fix.md` |
| `57-workbuddy--claim-denum-coordinator-review.md` | `workbuddy--claim-denum-coordinator-review.md` |

同步 4 处自指（`git mv` 不会改正文）：

| 文件 | 处数 | 说明 |
|---|---|---|
| `docs/tasks/notes/deadlock-fix.md` | 2 | 认领登记行、自罚表 |
| `docs/incidents/mergeguard-open-defects-2026-10-07.md` | 1 | 报告内自指 |
| `docs/tasks/notes/claim-denum-coordinator-review.md` | 1 | 「允许修改目录」原写 `docs/claims/57-*.md`，迁移后该路径不存在 |

### 遗留：还剩 58 个带前缀

全仓 60 个认领文件，本任务迁走 2 个，剩 58 个带 `NN-` 前缀
（最后一个是 `60-qoder--sweeper-aging-stuck.md`）。**各 owner 各迁各的**，
本任务不代劳（H-5：不碰他人文件）。

### 自罚：两笔连续踩「合并后才登记」

和 `deadlock-fix` 同一个坑——`registry` 组报
「未登记=['agent/workbuddy/claim-denum-migrate/a1']」，4 条断言全红：

```
FAIL  认领登记的任务都在任务表中；孤儿=['claim-denum-migrate']
FAIL  已认领且非在途的任务都有任务卡；缺卡=['claim-denum-migrate']
FAIL  已合入 main 的任务在任务表均已完成；未完成=['claim-denum-migrate']
FAIL  已合入 main 的任务均有任务卡；缺卡=['claim-denum-migrate']
```

正确顺序是「先建认领文件 + 任务卡 + 任务表行 → 再合并」。

**两笔连续踩同一个坑说明这条规则目前只有事后检查，没有事前阻断**——
合入前没有任何一步会问「你这个分支有认领文件吗」。
（是否加事前阻断属门禁改动，不在本任务允许修改目录内，仅记录不实施。）

### 合并期撞上别人的红灯（与本次改动无关）

本分支基于 `696eea8` 建立，提交时 main 已前进到 `2c85a9c`
（OpenCode 合入 `mergeguard-fix` 但没回填任务表、缺卡）。门禁红的三条
全部不是我的改动引起的：

| 失败项 | 归属 |
|---|---|
| 已合入 main 的任务在任务表均已完成；未完成=['mergeguard-fix'] | OpenCode |
| 已合入 main 的任务均有任务卡；缺卡=['mergeguard-fix'] | OpenCode |
| 根工作区有 MERGE_HEAD 但无锁持有者记录 | 合并期固有形态 |

与我的改动**零交集**（只动 2 个文件名 + 4 处自指，不碰他人文件）。

### 双向死锁的处置

撞上的是双向死锁：我的 MERGE_HEAD 占着根工作区 → OpenCode 合不了回填 →
门禁一直红 → 我也提交不了。

**处置**：`git merge --abort` 撤自己的合并现场（改动安全留在分支 `c9425aa`），
解开死锁；撤后第三条 rootworktree 红灯立刻消失，反证它是合并期固有形态。
等 OpenCode 回填后重新合并成功（`27c0eb1`）。

教训：**合并期撞红灯先判归属，别默认「先修自己的」**——
这里的正确动作反而是撤自己的现场给对方让路。

### 验证结果

- 迁移后 `docs/claims/` 无旧名残留（`56-` / `57-` 前缀均消失）
- 4 处自指全部同步，无悬空路径
- discipline 44/0、docstate 90/0、pytest 52 passed（48→52，方案 C 实施时新增 4 条断言）
