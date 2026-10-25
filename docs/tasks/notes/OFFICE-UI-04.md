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
当前状态：done（已合入 main：`96bdbaf`）
commit：`96bdbaf`（这一轮与前几轮一起合入 main）
验证结果：平台上的翻译：`backend/tests/test_wording.py` 31 项 + `office/scripts/verify-office-wording.mjs` 通过；真跑：失败原因、事件名、方案原文都成了人话；后端全量 pytest 全绿。
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
| **后端全量** `pytest` | **全绿**（退出码 0；这套在这台共享机器上要 40 分钟） |
| `office/scripts/verify-office-wording.mjs` | ok（Chromium 实测，拦截 API） |
| 办公室冒烟 | 22 条全绿（含新的一条） |
| `tools/docstate` / `tools/discipline` / `ruff` | 98 / 44 / 全过 |
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

**模型层（自由文本）**也真跑通了：把那份 4069 字的方案原文（满篇接口名、字段名、
快照 id、传阅记录）交给管理员自己的 CLI，翻成一句：

> 对照工位屏幕与平台数据，写清各项显示的来源、差异和无法读取的字段。

这句现在摆在**任务书顶上**（"这份方案讲的是：…"），下面才是原来的活账与方案原文。
翻译结果按文本 sha256 缓存，同一份方案不重复问模型。

## 踩到的三件事

1. **网关的 10 秒时限**：模型层要跑管理员那台 CLI（几十秒），网关那个 10 秒
   超时直接把它掐成 503。`/wording/summarize` 单独给 120 秒。
2. **摘要写在 DOM 里会被抹掉**：任务书每 15 秒跟着办公室重画一次，填进 DOM 的
   那句话下一轮就没了。摘要要存在 state 里（跟着方案原文走，方案换了才重翻）。
3. **两个冒烟的断言**：任务书里的人名从窗口 id 变成了登记名，断言跟着改 ——
   断的还是"接过的人看得见"这件事，不是他长什么样。

---

## 第二轮：输出窗口与平台助手的收尾（方向 4）

同一张卡里继续做"术语清理 + 真结论不被长文吃掉"。这一轮**先真跑再改**：对着
真平台（8849 / 8850 真数据副本）+ 真令牌把输出页与助手面板截下来看，静态断言
全绿但真跑抓出五个问题。

### 这一轮改了什么

| 处 | 原来 | 现在 |
|---|---|---|
| 输出页的结论 | 386 行整段摆出来 | 前 6 行 + 「它还写了 N 行」折叠 |
| 输出页的失败原因 | `formal worker error: HarnessLeaseLost` | 平台内部出错了（租约丢了（…）） |
| 输出页的 stderr | 一段红字，没有标题 | 「它报的错」+ 去 markdown 标记 |
| 平台助手的方案正文 | Markdown 源码整篇 | 去标记 + 前 12 行 + 「方案还有 N 行」折叠 |
| 平台助手的术语 | 管理员 Harness / 记录键 N 个 / kind=development | 管理员窗口 / 记了 N 条进度 / 开发中的那一种 |
| 额度表的「说明」列 | 英文（`the provider the Claude CLI is configured to use`） | 中文（`Claude CLI 自己配的那个 provider`） |
| 助手面板配色 | 一套遗留的绿色（`#fcfaf6`/`#243b33`） | 产品外壳那套蓝灰（`--ao-*` 变量，带兜底值） |
| 窗口 id | 「执行窗口：opencode」 | 「执行窗口：OpenCode」（平台名册查得到） |

平台侧（`backend/app/wording/`）新增规则：

- `WORKER_ERROR_TEXT`：平台内部错误码 → 人话（`HarnessLeaseLost` → 租约丢了）；
  认不出的代码**原样留着**（`RuntimeError`），宁可给个能搜的词也不编。
- 自由文本（`kind=text`）把机器标记换成能读的说法：任务号 → 这条活、
  `attempt .a1` → 第 1 轮、`exclusive_paths` → 允许改的路径、hash 缩短到 7 位。
  **只动标识符，不动英文散文**（"commit" 当动词用时替掉会把句子改坏）。
- Windows 的退出码是无符号的：`-1` 传上来是 `4294967295`，转回负数。
- 「执行窗口：<id>」这类标签后面的窗口 id，换成平台名册里的登记名。

### 真跑抓到的五个问题（静态断言全绿时没发现）

1. **`original` 被 strip 过** —— 平台回显的原文去了首尾空白，方案正文末尾带换行，
   网关按原文当钥匙回填就**找不到**，洗过的人话回填不回去（方案里的 `FD-…` 原样
   露着）。改：平台**一字不动**回显调用方给的原文。
2. **名册查错了表** —— 窗口名在 `harness_registrations`，不在 `harnesses`；读错表
   抛异常被 `except` 吞掉，窗口名整批消失（"执行窗口：opencode"没换）。
3. **重复的解释** —— 真数据那句是"改动只落在 `exclusive_paths` 唯一允许的路径上"，
   直接替换成"允许改的路径"变成"落在 允许改的路径 唯一允许的路径上"（同一件事
   说两遍）。改：这一整段折成一句人话。
4. **两处环境依赖的假红** —— `verify-platform-assistant` 与 `verify-office-layout`
   红了，其实是 4174 预览**没带网关环境变量**、平台实例没开 `AP_AUTH_MODE=enforce`，
   全是 503。查清归属后重启我的实例就好，codex 的 8848 没碰。
5. **面板配色跟产品不是一套** —— 助手面板还是遗留的绿色，产品的外壳已经是蓝灰。

### 验证结果

| 门禁 | 结果 |
|---|---|
| `backend/tests/test_wording.py` | 31/31 PASS |
| **后端全量** `pytest` | **全绿**（退出码 0；这套在这台共享机器上要 40 分钟） |
| 办公室冒烟 | 23/26 通过（3 条是环境依赖，见下） |
| `tools/docstate` / `tools/discipline` / `ruff` | 98 / 44 / 全过 |
| 真跑 | 8850 真数据副本（18 次尝试）+ 8849 我的实例（74 条活） |

**环境依赖、不是代码问题**：`verify-anchors`（要 5197 端口的 Godot 原型 dev server）、
`verify-harness-connections`（要本机装好的 CLI 与 Ollama 的 `qwen3:4b`）、
`verify-project-workspace`（断言"平台在册有凭据的窗口数是 9"，真平台 8848 是 9 个、
我的实例只有 7 个）。

### 这一轮踩到的第五件事：浏览器里 `names` 是内建属性

面板里要把窗口 id 换成登记名，就写了 `names.get(harness_id)` —— 但 `names` 只是
巡检函数里的局部量。浏览器里 `names` 是 `window.names`（frame 名列表），**不报
未定义**，等到真调 `.get` 才炸；当时没炸是因为那一节的候选列表恰好是空的。
改成统一走 `nameOf(id)`（读 `snapshot.names`），并把面板里其余几处也一并换掉。




