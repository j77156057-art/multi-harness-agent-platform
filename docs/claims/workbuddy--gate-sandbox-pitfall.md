# 认领：gate-sandbox-pitfall

| 字段 | 值 |
|---|---|
| task_id | `gate-sandbox-pitfall` |
| Harness | WorkBuddy |
| 认领时间 | 2026-10-07 09:25 |
| 分支 | `agent/workbuddy/gate-sandbox-pitfall/a1` |
| 状态 | ✅ done（内容提交 `8860052`） |
| 工作区 | `.worktrees/workbuddy` |

任务表对应行：`TASKS.md` → 「任务表」，搜 `gate-sandbox-pitfall`。

## 任务

把两条**实测得出、目前只存在于对话里**的结论回填进仓库文档：

1. 在 WorkBuddy harness 里跑 `pytest` 必须走沙箱外（否则被safe-delete 钩子打死）
2. 认领序号撞号在 main 上是**既成事实**（两组重复前缀），不是风险预测

## 为什么值得占一个task

两条都是「每个 AI 都会踩、但踩了不知道为什么」的坑：

| 坑 | 后果 |
|---|---|
| 门禁跑不起来 | 看到 `SystemExit: 1` 会误判成测试挂了，开始改断言——而真实原因在环境层 |
| 撞号无感 | 序号在main 上已经重复两组，而门禁只查 `task_id`，绿灯不含信息 |

第2 条尤其要紧：它**修正了我自己在 `claim-denum-coordinator-review` 卡里的论证强度**。
卡里写的是「10 次里9 次要重编号」——那是过程统计；现在是main 上的实物证据。

## 改了什么

| 文件 | 改动 |
|---|---|
| `docs/dev-convention.md` | 「提交前自检」节加「在 WorkBuddy harness 里跑门禁」小节 |
| `docs/tasks/notes/claim-denum-coordinator-review.md` | 「撞号频率」节后追加 main 上的既成事实 |

## 边界

-只改我自己名下的文档 + `docs/dev-convention.md`（共享文件，已按 H-8 查过）
- 不碰 `tools/**`（沙箱外跑 pytest 是**使用方式**的约定，不是改门禁代码）

## H-8 前置检查（已做）

`git diff --name-only main...<branch>`逐个查在途分支，**只有 1 个分支**对这两个
文件有独有改动——而且是**我自己**的 `agent/workbuddy/claim-denum-coordinator-review/a1`
（过时副本，无人协作风险）。无第三方在途冲突。

## 自罚记录（前两笔的延续）

`deadlock-fix` 与 `claim-denum-migrate` 连续两次「合并后才登记」。本任务在
**动手写内容之前**先建认领文件与任务卡 —— 顺序纠正。