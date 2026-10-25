# 认领：OFFICE-UI-26

| 字段 | 值 |
|---|---|
| task_id | `OFFICE-UI-26` |
| Harness | Mavis |
| 认领时间 | 2026-10-25 10:25 +08:00 |
| 分支 | `agent/local/mavis/OFFICE-UI-26/a1` |
| 状态 | doing |

目标：**每个 harness 的用量与缓存命中率都看得见，并且看得出它的出处。**

为什么：仓库主人问「抓不到缓存命中率吗」—— 并选定「(a) + (c) 一起做」。

现在的问题：办公室窗口页的「用量」块读的是**额度池的用量账**（consumer =
`harness/<id>`），所以**只有走额度池的窗口才有这一块**；成员用**自带模型**跑的活，
平台不在数据链路上，界面上一片空白 —— 人以为「没消耗」，其实只是没记账。

事实（真机查的）：
- CLI 的原始输出里**有** usage，也**带缓存字段**（Anthropic 形状的
  `cache_read_input_tokens`、opencode 形状的 `tokens.cache.read`、OpenAI 的
  `prompt_tokens_details.cached_tokens`）。真数据里 Claude Code 那次跑完的汇总是：
  输入 49,818 · **缓存读 1,182,464** · 输出 35,543 —— 这在界面上一个数都看不到。
- 成员自报的完成回执里**没有** usage 字段（真数据里 `harness_output` 只有
  `final_message` 与 `session_id`），所以平台得**自己从 CLI 的输出里解析**。
- 平台的**数据目录里就有每个 attempt 的 CLI 原始输出**（`data/formal-runs/<任务>.<轮次>.log`），
  办公室的「它最后说了什么」读的就是它。

范围：

- `backend/app/usage/cli_reported.py`（新）：从 CLI 输出解析 usage（认三种形状；
  认不出就是「没有」，不写 0），并记账。
- `backend/app/tasks/service.py`：attempt 结束（提交结果 / 放弃）时补记一笔。
- `backend/app/pool/service.py`：`usage_summary()` 按来源分开 —— **额度池的**与
  **成员自报的**各自一栏，「额度池」那个视图不被污染。
- `office/src/flat-office.js`：窗口页的「用量」块写清出处，两类数都摆出来。

**不碰** `docs/spec/**`、不动其他 Harness 的工作区与分支。

## 怎么做到不出错

1. **不造数**：解析不出来就是「没有数据」，不写 0（仓库的老规矩：宁可原样，也不编）。
2. **口径分开**：额度池的用量与成员自报的用量是**两笔账**，界面上分开摆、写清各是什么；
   「额度池」那栏只算额度池的。
3. **不动在途文件**：H-8 查过 —— `codex-model-switch` 在途改的是
   `backend/app/api/routes.py`、`backend/app/adapter/*`；本卡**不碰这些**。
4. **先原型验证再动手**：解析器先在真日志上跑过（三种形状、重复行不重复计、
   认不出就是 None），再进仓。
