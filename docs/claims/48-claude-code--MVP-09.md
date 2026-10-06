# 认领：MVP-09

<!-- 本文件由 Claude Code 创建（拆分之后的格式）。其他 AI 请勿修改本文件；
     改你自己的即可，永不冲突。 -->

| 字段 | 值 |
|---|---|
| task_id | `MVP-09` |
| Harness | Claude Code |
| 认领时间 | 2026-10-06 09:03 |
| 分支 | `agent/local/claude/MVP-09/a1` |
| 状态 | 🟢 doing |

任务表对应行：`TASKS.md` 「任务表」章节，`MVP-09`。

## 认领前的全仓扫描（采纳 dup-incident 事故的建议）

只信 main 的登记表会漏掉在途认领 —— 这正是 2026-10-06 那次重复劳动的成因。
本次认领前先扫全仓：

~~~text
git branch -a | grep MVP-09        → 无
git branch -a | grep frontend      → 无
git log --all --grep="MVP-09"      → 3 条，全是文档提及（traceability/任务表），无实现
git log --all -- frontend/         → 仅 810d88d（MVP-01 骨架）
~~~

结论：MVP-09 确实无人开工。**这条扫描已写进事故记录的补救建议，本次是第一次实际使用。**

## 形态决策：CLI 代替界面（经用户确认）

清单对 MVP-09 写明：

> 若要压缩时间轴，可**整体后置**到 MVP-11 之后，先用一个 **CLI 脚本代替人工操作**
> —— 因为 MVP 的验证目标是协调语义，不是界面。

而完成标准「**人类不直接访问数据库即可完成一条完整任务链路**」不要求浏览器。

两点支撑这个选择：
- **CI 完全没有前端门禁**（`gate.yml` 无 npm/node 步骤）。一个无门禁、无测试的 Vue UI
  按 `traceability.md` 自己的标准会落进「仅是名字」那一类，无法被验证
- CLI 可以直接用 pytest 驱动，是可机械验证的

## 认领时发现的两处 API 缺口（记录下来，不自补端点）

| 缺口 | 影响 |
|---|---|
| **`projects` / `groups` 只有 POST，没有 GET** | CLI 无法「列出项目/群组」，只能创建 |
| **Merge Gate 没有任何 HTTP 路由** | `app/merge/{gate,worker}.py` 由 MVP-07 交付，但未暴露；清单要「Merge Gate 页面」，而完成标准禁止绕过 API —— 因此 merge **无法在不违反该约束的前提下由 CLI 驱动** |

按先例（Adapter 工作中发现 §7 缺 verb 时不自造 verb），本任务**记录缺口而不新增端点**：
新增端点属 MVP-07/08 的范围，而两者均已验收（H-5：跨边界改动需 owner 同意）。

MVP-09 的完成标准是「建群 → 发方案 → 领取 → 提交 → 审查」链路，**不含 merge**，
所以现有端点在范围内够用。
