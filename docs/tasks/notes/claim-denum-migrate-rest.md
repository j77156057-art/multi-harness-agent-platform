### claim-denum-migrate-rest：WorkBuddy 剩余 11 个认领文件去前缀（🔄 doing）

~~~text
任务编号：claim-denum-migrate-rest
负责人 Harness：WorkBuddy
开始时间：2026-10-07 09:50
工作区：.worktrees/workbuddy
依赖任务：claim-denum-migrate
真相源引用：docs/proposals/claim-denum.md（OpenCode，方案 C 原文）
            docs/claims/workbuddy--claim-denum-migrate.md（C2 文件片，已完成两个）
允许修改目录：docs/claims/workbuddy--*.md（本人名下）、docs/tasks/notes/ 下本人卡片、docs/proposals/claim-denum-coordinator-review.md、TASKS.md任务表与认领表注释
禁止修改目录：tools/**、backend/**、docs/spec/**、docs/claims/ 下他人文件
交付分支：agent/workbuddy/claim-denum-migrate-rest/a1
test_id：MVP-PROCESS-001
当前状态：done
完成时间：2026-10-07 10:05
commit：`5f25665`（11 个改名 + 21 处活指针同步）
验证结果：见下方「验证结果」
~~~

### 先纠正一个我自己的错误

上一轮我在状态汇报里说「WorkBuddy 名下 3 个认领文件，已全迁完」。**这是错的。**

|口径 | 数 |
|---|---|
| 我名下认领文件**总数** | **15** |
| 当时已迁（无前缀） | 3 |
| **当时待迁** | **12** |

错在哪：我数的是「全仓库已迁移了几个」，然后当成了「我名下有几个」。
**用一个对自己有利的口径替代了真正的口径**——这跟 `deadlock-fix` 里记的
「两笔连续踩同一个坑」是同一类毛病。

**报数必须报对自己有利的那个分母，先把分母换成不利的那一个。**

### 范围与所有权边界

main 上共 **27 处**对带前缀 workbuddy 认领的引用，分布：

| 类别 | 处数 | 处置 |
|---|---|---|
| 我名下认领文件 + 我自己的卡/提案 | 14 | ✅ 改 |
| `TASKS.md` 认领表注释行 | 7 | ✅ 改（**必须**） |
| 别人文件里的散文引用 | 3 | ❌ 不动（H-5） |
| 门禁代码注释 | 3 | ❌ 不动（禁止修改 `tools/**`、`backend/**`） |
| 迁移前→后对照记录 | 4 | ❌ **故意不改** |

#### TASKS.md 那 7 条为什么必须改

`discipline` 有断言「认领登记表注释行指向的文件均存在」。
`TASKS.md` 里 `<!-- MVP-01 → docs/claims/01-workbuddy--MVP-01.md -->` 这类
注释行是**机器校验项**，改名不同步直接判红。

其余散文引用不会判红，只是变陈旧——**「不判红」不等于「可以放着不管」**，
如实列在下面。

### 改了什么

11 个文件改名（`git mv`，保留历史）：

| 迁移前 | 迁移后 |
|---|---|
| `01-workbuddy--MVP-01.md` | `workbuddy--MVP-01.md` |
| `09-workbuddy--fix-wal-lock-retry.md` | `workbuddy--fix-wal-lock-retry.md` |
| `11-workbuddy--R10-fix-10.md` | `workbuddy--R10-fix-10.md` |
| `14-workbuddy--fix-flaky-init.md` | `workbuddy--fix-flaky-init.md` |
| `19-workbuddy--claim-nullable.md` | `workbuddy--claim-nullable.md` |
| `21-workbuddy--verify-mvp-04.md` | `workbuddy--verify-mvp-04.md` |
| `24-workbuddy--claims-split.md` | `workbuddy--claims-split.md` |
| `45-workbuddy--chain-template-closeout.md` | `workbuddy--chain-template-closeout.md` |
| `47-workbuddy--slow-test-visibility.md` | `workbuddy--slow-test-visibility.md` |
| `52-workbuddy--report-mergeguard-commit-bug.md` | `workbuddy--report-mergeguard-commit-bug.md` |
| `55-workbuddy--tasks-split.md` | `workbuddy--tasks-split.md` |

同步 **21 处**自指：`TASKS.md` 7 处注释行 + 我名下 14 处。

### 故意不改的：迁移对照记录

`claim-denum-migrate.md`（认领 + 卡片）里有这样的表：

| 迁移前 | 迁移后 |
|---|---|
| `56-workbuddy--deadlock-fix.md` | `workbuddy--deadlock-fix.md` |

**这 4 处旧名必须留着** —— 它是「改了什么」的证据，改掉就把审计线索毁了。
**迁移记录里的旧名不是陈旧引用，是历史。**

### 遗留：如实记录，不假装做完

改名后以下 6 处引用会指向不存在的路径，**都是别人的文件或门禁代码**：

| 位置 | 指向 |
|---|---|
| `docs/claims/49-opencode--claim-denum.md:52` | `47-workbuddy--slow-test-visibility.md` |
| `docs/claims/54-opencode--root-worktree-clean.md:39` | `52-workbuddy--report-mergeguard-commit-bug.md` |
| `docs/tasks/notes/MVP-10b-verify.md:99`（TRAE） | `45-workbuddy--chain-template-closeout.md` |
| `tools/discipline/test_discipline.py:108,113` | `01-workbuddy--MVP-01.md`（测试夹具里的合成字符串） |
| `tools/mergeguard/cli.py:318` | `52-workbuddy--report-mergeguard-commit-bug.md`（注释） |
| `backend/tests/test_merge_guard.py:855` | 同上（注释） |

其中 `test_discipline.py` 的两处**是合成测试数据**（自造TASKS.md 片段），
不读真实文件，改不改都不影响断言。其余 4 处是纯注释/散文。

**需要 owner 自己改，或者协调者授权。代改就是越界。**

### 验证结果

- 11 个文件改名全部被 git 识别为 `R`（rename，历史保留，无新增/删除对）
- 我名下文件（认领 + 卡 + 提案 + TASKS.md）**零残留**旧名
- `discipline` **44/0**、`docstate` **90/0**、pytest **52 passed**（沙箱外）
- 剩余 6 处指向旧名的引用全部在**别人文件或门禁代码**里，已如实列在上面，
  未假装做完