# 认领：claim-denum-migrate-rest

| 字段 | 值 |
|---|---|
| task_id | `claim-denum-migrate-rest` |
| Harness | WorkBuddy |
| 认领时间 | 2026-10-07 09:50 |
| 分支 | `agent/workbuddy/claim-denum-migrate-rest/a1` |
| 状态 | 🔄 doing |
| 工作区 | `.worktrees/workbuddy` |

任务表对应行：`TASKS.md` →「任务表」，搜 `claim-denum-migrate-rest`。

本任务虽与 `gate-sandbox-pitfall`（`30e9a71`）在同一 worktree 连续作业，
但**有独立分支**——认领的「分支」字段填已合入 main 的分支会让 merge 组
立刻要求本任务为 done，属于用登记字段撒谎。

## 纠正：上一轮我报的「只剩 3 个」是错的

`claim-denum-migrate` 的卡里写「本任务只做 WorkBuddy 那两个」，
合入后我在状态汇报里说「WorkBuddy 名下 3 个认领文件，已全迁完」。

**实际是错的。** 当时我数的是「全仓库已迁移的」，不是「我名下待迁移的」：

| | 数 |
|---|---|
| 我名下认领文件总数 | **15** |
| 当时已迁（无前缀） | 3 |
| **当时还没迁** | **12** → 本任务做完剩 11 → 现已全迁 |

这跟`deadlock-fix` 记的那笔「两笔连续踩同一个坑」是同一类错误：
**用了一个对自己有利的口径，替代了真正的口径。**

## 任务

把剩下 11 个 WorkBuddy 认领文件去掉序号前缀，同步所有**我有权改**的自指。

## 所有权边界（这决定了本任务只做一半）

| 类别 | 处数 | 处置 |
|---|---|---|
| 我名下认领文件 + 我自己的卡/提案 | 14 | ✅ 本任务改 |
| `TASKS.md` 认领表注释行 | 7 | ✅ 本任务改（**必须改**，见下） |
| 别人文件里的散文引用 | 3 | ❌ 不动（H-5）：`49-opencode--claim-denum.md`、`54-opencode--root-worktree-clean.md`、`MVP-10b-verify.md`(TRAE) |
| 门禁代码注释 | 3 | ❌ 不动：本卡禁止修改 `tools/**`、`backend/**` |
| 迁移前→后对照记录 | 4 | ❌ **故意不改**：改了破坏「迁移前 / 迁移后」对照 |

### 为什么 TASKS.md 那 7 条必须改

`discipline` 有断言「认领登记表注释行指向的文件均存在」。
`TASKS.md` 里形如 `<!-- MVP-01 → docs/claims/01-workbuddy--MVP-01.md -->`
的注释行是**机器校验的**，改名不同步就会判红。其余散文引用不会判红，
只是会变陈旧。

## 验证

- 门禁：discipline 44/0、docstate 90/0、pytest 52 passed（沙箱外）
- 11 个文件全部改名成功，14+7 处自指同步
- 剩余 3 处陈旧引用（别人文件）与 3 处门禁代码注释**如实报告，不假装做完**