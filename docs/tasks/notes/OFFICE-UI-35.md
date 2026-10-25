# OFFICE-UI-35

### 统一任务卡

任务编号：OFFICE-UI-35
负责 Harness：Mavis
开始时间：2026-10-25 21:35 +08:00
工作区：`.worktrees/mavis/`
依赖任务：OFFICE-UI-31（助手的深取证档）—— 与 UI-32/33/34 同一层：UI-33 已在链长 9
的末端，再挂上去会超过清单 §0.2 声明的长度
真相源引用：`backend/app/assistant/tools.py`（工具的返回形状）、
`backend/app/assistant/loop.py`（引擎，只读）、`backend/app/assistant/prompts.py`（姿势，
只读）、`docs/dev-convention.md` H-4 / H-7 / H-8
当前状态：done
commit：`3c3ea23`（助手回答不再半截：group.tasks 摘要化 + 可分页）
验证结果：见文末「验证结果」表。
test_id：MVP-UI-050

| 项 | 内容 |
|---|---|
| 依赖 | OFFICE-UI-31 |
| test_id | `MVP-UI-050` |
| 允许修改 | `backend/app/assistant/tools.py`（**只改 `group_tasks` 一个函数**）、`backend/tests/**`（新文件）、`docs/claims/mavis--OFFICE-UI-35.md`、`docs/tasks/notes/OFFICE-UI-35.md`、`docs/traceability.md`、`TASKS.md` |
| 禁止修改 | `backend/app/assistant/loop.py`、`backend/app/assistant/prompts.py`、`backend/app/assistant/worker.py`、`backend/app/assistant/memory.py`、`backend/app/assistant/runs.py`、`backend/tests/test_assistant_loop.py`（codex 在途）、`docs/spec/**`、其他 Harness 的工作区与分支 |

## 做什么

| 原来 | 现在 |
|---|---|
| `group.tasks` 把本群几十条任务的**全字段**整份塞进一次观察 → 模型只看到前一半 → 回答里出现「在 FD-76… 处被截断」 | 一次看到**完整摘要**：总数、按状态分布、没被领的与已有人领的清单（少量字段）、**明确省略了多少条**；要全量可以 `limit` / `offset` 翻页 |

## 为什么

仓库主人指出「怎么回答后面都有省略号，回答完了吗」—— 查下来平台里的原文是完整的，被切
的是它看到的那份工具观察。助手没有编（它明说「未记上，也不补编」），但体验上就是
「没答完」。

## 怎么做到不出错

1. 只改一个函数的返回形状：引擎、提示词、工具名单一律不动。
2. 摘要里说清省略了多少条、怎么翻页（别把「没列全」变成另一种看不见的截断）。
3. 助手仍然只读。

## 完成标准

1. 回答里不再出现半截清单；要么完整摘要，要么明说「共 N 条、列了 M 条、要全量请翻页」。
2. `group.tasks` 支持 `limit` / `offset`。
3. 后端全量 pytest 通过；门禁全绿；办公室冒烟全绿。
4. `docs/traceability.md` 登记 `MVP-UI-050`。

## 验证结果

| 门禁 | 结果 |
|---|---|
| 后端新增单测 | `backend/tests/test_assistant_group_tasks.py` 新增 7 项（观察是摘要不是全表、谁持有什么、清单只带必要字段、分页真的传给平台、怪 limit 回落默认、status 过滤仍可用、坏 status 被拒）；助手全套 45 项全绿（**含 codex 的 `test_assistant_loop.py`**） |
| 根因（查出来的） | 平台消息里的原文是**完整的**（583 字符、有结尾）—— 不是界面截的。被切的是**它看到的那份工具观察**：`group.tasks` 把本群 74 条任务的**全字段**整份塞进一次观察（几十 KB），模型只看到前一半，于是它如实说「完整 ID 清单未记上…随后在 **FD-76…** 处被截断」。助手没有编（它明说「其余条目的完整 ID 我未记上，也不补编」），但体验上就是「没答完」 |
| 真跑（8849 真平台 + 4174 预览） | 问「本群按状态各有多少条任务？请重新取一次数据」→ 助手答：「本群共 74 条任务，按状态分布：cancelled 25、ready 21、abandoned 10、merged 6、draft 4、stale 3、escalated 2、changes_requested 2、in_progress 1[s1]。九项相加为 74，与 total 一致[s1]…该工具是摘要窗口，本次只列出 20 条明细、未列 54 条，但状态分布与总数是整表统计[s1]；如需逐条明细，需按 offset/limit 翻页[s1]」—— **没有「在 FD-76… 处被截断」了**：分布是整表的，缺多少条说清楚、怎么翻页也说清楚 |
| 办公室全部冒烟 | 32/35（3 条是与本卡无关的环境依赖：verify-anchors、verify-harness-connections、verify-project-workspace，与 OFFICE-UI-28~34 同） |
| `tools/docstate` / `tools/discipline` / `ruff` | 98/98、44/44；`ruff check` + `ruff format --check` 全绿 |
| `pytest`（全量） | EXIT=0 |
| 边界 | 引擎 `loop.py`、提示词 `prompts.py`、工具名单一律没动，只改 `group_tasks` 一个函数的返回形状。分页参数**只有模型显式给了才往平台传**（不传时请求形状与从前一模一样 —— codex 的 `test_assistant_loop.py` 精确断言了请求参数，不能被改坏）。平台的 `GET /groups/{group_id}/tasks` 默认**不传就仍是全量**，既有调用方不受影响 |
