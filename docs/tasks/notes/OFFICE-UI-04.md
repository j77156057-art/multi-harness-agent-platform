# OFFICE-UI-04

### 统一任务卡

任务编号：OFFICE-UI-04
负责 Harness：Mavis
开始时间：2026-10-23 13:50 +08:00
工作区：`.worktrees/mavis/`
依赖任务：OFFICE-UI-02（办公室界面）、OFFICE-UI-03（额度池那一轮）
真相源引用：R10 §17（权限矩阵：谁能让人翻）、§26（检查追溯）；
`backend/app/admin/service.py`（管理员的模型通道）；
`docs/dev-convention.md` H-4 / H-7 / H-8
当前状态：doing
test_id：MVP-UI-018

| 项 | 内容 |
|---|---|
| 依赖 | OFFICE-UI-02 |
| test_id | `MVP-UI-018` |
| 允许修改 | `backend/app/wording/**`（新）、`backend/app/api/app.py`、`backend/app/auth/policy.py`（只加一行键）、`backend/app/admin/service.py`、`backend/tests/test_wording.py`、`office/**`、`docs/claims/mavis--OFFICE-UI-04.md`、`docs/tasks/notes/OFFICE-UI-04.md`、`docs/traceability.md`、`TASKS.md` |
| 禁止修改 | `backend/app/api/routes.py`、`backend/app/tasks/**`、`backend/app/claim/**`、`docs/spec/**`、`backend/migrations/**`、其他 Harness 的工作区与分支 |

## 做什么

平台自己会把**机器写的文本**翻成人话，界面上只显示人话：

1. **规则层**（确定性的、永远可用）：
   失败原因（`harness CLI exited 1`、`harness timeout: …`）、事件名
   （`submit_result` → 交上去了）、状态、窗口 id（→ 注册表里的登记名）、
   绝对路径（→ 文件名）、commit / 分支 / 任务号（→ 短形式）。
2. **模型层**（自由文本）：方案原文、规格正文这种"源码一样"的段落 ——
   走管理员自己的模型通道翻成一句人话，**带缓存**（同一份文本只问一次）；
   通道不可用就**明说翻不了**，不编。

## 为什么放在平台

这些文本的**出处**在平台：失败原因是平台写的、事件名是平台的事件、窗口的登记名
在平台的注册表里。人话表现在散在前端三处（`state-labels.js`、
`admin-agent.js` 的 `REASON_LABEL`、`flat-office.js` 的 `reasonText`）——
改一次要改三处，而且各翻各的。翻译放在出处那一层，界面只负责显示。

## 怎么做到不出错

1. **认不出的原样留着**：规则表里没有的文本**照实显示**，并在结果里标
   `translated: false` —— 宁可原样，也不要编一个错的说法。
2. **模型层不许编**：模型翻不出来（通道不可用、超时、没给 JSON）就报明确原因，
   界面显示原文，不显示半句假话。
3. **不碰别人的在途文件**：`backend/app/api/routes.py`（opencode 在改）、
   `backend/app/tasks/**`（codex 在改）一律不碰；新功能全在新目录里，
   `app.py` 只加两行、`policy.py` 只加一行。

## 完成标准

1. `backend/tests/test_wording.py` 绿：规则层（每类机器文本各一条 + 认不出的
   原样返回）、模型层（通道不可用时明确失败、不编）、HTTP 面（权限、批量）。
2. 办公室真的用上了：失败原因、事件名、路径在界面上是人话；机器那一份在
   title / "技术信息"里。
3. 真平台实跑：对着真数据翻一遍（真失败原因、真事件名）。
4. 既有冒烟全绿；`office/` 构建通过；`./scripts.sh check` 三道门禁全绿。
5. `docs/traceability.md` 登记 `MVP-UI-018`。

## 验证结果

| 门禁 | 结果 |
|---|---|
| `backend/tests/test_wording.py` | 15/15 PASS |
| 翻译 + 池子 + 权限矩阵 | 94/94 PASS |
| `office/scripts/verify-office-wording.mjs` | ok（Chromium 实测，拦截 API） |
| 其余办公室冒烟 | 22 条全绿（含新的一条） |
| 真平台实跑 | 见下 |

### 真跑记录（真平台 8849 + 真令牌 + 真数据）

真数据里 **7 条失败原因、25 种事件名**。翻译之后：

- `harness CLI exited 1` → 窗口的命令退出了（退出码 1）
- `harness timeout: … exceeded 1800s` → 窗口跑超时了（超过 1800 秒没做完）
- `formal worker error: HarnessLeaseLost` → 平台内部出错了（HarnessLeaseLost）
- `cancel` → 取消了；`plan.approve` → 方案批准了；`submit_result` → 交上去了；
  `escalation_reminder` → 提醒管理员了 …
- `D:\study\tools\agent\new agent\.worktrees\mavis\data\exports\plans\plan-….md`
  → `plan-….md`（只留文件名，完整路径在 title 里）
- **认不出的原样留着**：一句已经是中文的说明（"上一次因为平台工位容量不足没开工…"）
  原样返回，没有被"翻译"成另一句话。

界面上：任务列表写「窗口的命令退出了（退出码 1）」，事件行写「10/18 20:20 ·
取消了 · 你」，机器那一份在 title 里（事件编号、原文、完整时间）。

