# 认领：claim-denum-coordinator-review

| 字段 | 值 |
|---|---|
| task_id | `claim-denum-coordinator-review` |
| Harness | WorkBuddy |
| 认领时间 | 2026-10-07 01:45 |
| 分支 | `agent/workbuddy/claim-denum-coordinator-review/a1` |
| 状态 | 🟢 doing（只交付裁决材料，不实施迁移） |
| 工作区 | `.worktrees/wbcd/` |

任务表对应行：`TASKS.md` → 「任务表」，搜 `claim-denum-coordinator-review`。

## 任务

对 `docs/proposals/claim-denum.md`（OpenCode，2026-10-06）做**事实核实与代价量化**，
产出协调者可直接裁决的材料。**不裁决、不实施迁移。**

触发：协调者 2026-10-07 01:43 指示「序号 54 归谁……建议改成无序号
（方案 C 已交付待裁决）」，并转述了一个关于合并现场的判断。两件事都需要先核实。

## 交付物

`docs/proposals/claim-denum-coordinator-review.md`

## 核实到的三件事（与提案原文不同的部分）

### 一、协调者转述的合并现场不存在了

转述称「WorkBuddy 的合并现场，TTL 还需约 7 分钟，等 mergeguard 接管或由他本人
收尾，别让第三个人去abort」。实测：`.git/MERGE_HEAD` 不存在，
工作区干净，main 在 `82a3a4a`。

那个 MERGE_HEAD 是我自己留下的（mergeguard 遇冲突退出、未 stage 解决结果），
我已在同一轮手工解冲突并提交（`2a16119` + `82a3a4a`）。**TTL 到期前就收掉了。**

### 二、54/55 的撞号也已在 main 上解决

协调者说「谁改成 55 由协调者定」。实际已经改了，因为不改发不出去。
但这个 55 是**过渡态**——方案 C 若落地，55 个文件都要改名，
所以不建议为它再耗一次协调。

### 三、提案低估了工作量：占号机制与拆分强耦合

提案迁移清单第 2 项写「`discipline.py` 改 1 行+ 删 2 条断言」。实测远不止：

| 组件 | 体量 | 提案提到 |
|---|---|---|
| `_reserved_claim_slots()` | ~35 行 | ❌ |
| 「序号唯一」断言 | ~10 行 | ✅ |
| `existing_seqs`/`by_seq`/撞号断言 | ~18 行 | ❌ |
| 路径格式断言 | ~8 行 | ❌ |
| TASKS.md 占号节 + 说明 | ~34 行 | ⚠️ 只提表格注释 |

那 35 行是**我昨天为拆分加的**（`e24be3a`）。占号机制是两天内长出来的，
存在成本比提案预估高一个量级。方案 C 落地后这些**整体删除**——
门禁净减 ~105 行断言，登记流程净少一步。

## 补测的前置条件（提案当天没测的那一项）

| 检查项 | 提案当天（40 个） | 今天（55 个） |
|---|---|---|
| task_id 两两不同 /大小写折叠后不同 | ✅ | ✅ |
| **正文 task_id ↔ 文件名 task_id 一致** | 未测 | ✅ **55/55 零不一致** |

第三项是重命名能否机械执行的关键：discipline 已有该断言（claim-seq-adopt
交付），所以重命名有守卫，不是人眼活。

## 撞号频率更新

提案写「8 次里 7 次重编号」。**我这一轮又添 2 次**（53 被 Qoder 占 → 54；
54 被 OpenCode 占 → 55），累计 **10 次里 9 次重编号**，且两次都严格走完 H-8。

这是方案 C 最硬的论据：取号与写入之间没有原子性，流程补不上这个缺口。

## 允许修改目录

`docs/proposals/**`、本认领文件、`TASKS.md` 任务表与占号节

## 禁止修改目录

`tools/**`、`backend/**`、`docs/spec/**`、`docs/claims/` 下他人文件、
`docs/tasks/notes/` 下他人卡片

**本任务不改门禁** —— 删占号机制需协调者点头（那部分是我加的）。
`55-workbuddy--tasks-split.md` 是我自己的文件，但本任务也不动它：
方案 C 若落地它连同其余 54 个一起改名，单独改没有意义。

## test_id

MVP-PROCESS-001

## 工作区

`.worktrees/wbcd`（H-7：不碰根目录）