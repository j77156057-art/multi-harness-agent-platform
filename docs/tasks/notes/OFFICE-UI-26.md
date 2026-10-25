# OFFICE-UI-26

### 统一任务卡

任务编号：OFFICE-UI-26
负责 Harness：Mavis
开始时间：2026-10-25 10:25 +08:00
工作区：`.worktrees/mavis/`
依赖任务：OFFICE-UI-25（文案书面化）、OFFICE-UI-17（token 度量）
真相源引用：`backend/app/pool/service.py`（用量账与汇总）、`backend/app/tasks/service.py`（完成回执与结算）、
`backend/app/adapter/cli_output.py`（CLI 原始输出的落盘位置）、`office/src/flat-office.js`（窗口页的用量块）；
`docs/dev-convention.md` H-4 / H-7 / H-8
当前状态：done（已合入 main：`4e7e4d4`）
commit：`aca4704`（成员自报的用量与缓存命中率看得见：两笔账分开摆，界面写清出处）
验证结果：见文末「验证结果」表。
test_id：MVP-UI-041

| 项 | 内容 |
|---|---|
| 依赖 | OFFICE-UI-25、OFFICE-UI-17 |
| test_id | `MVP-UI-041` |
| 允许修改 | `backend/app/usage/**`（新）、`backend/app/tasks/service.py`、`backend/app/pool/service.py`、`backend/tests/**`、`office/src/flat-office.js`、`office/scripts/**`、`docs/claims/mavis--OFFICE-UI-26.md`、`docs/tasks/notes/OFFICE-UI-26.md`、`docs/traceability.md`、`TASKS.md` |
| 禁止修改 | `docs/spec/**`、`backend/app/api/routes.py`、`backend/app/adapter/**`（codex 在途）、其他 Harness 的工作区与分支 |

## 做什么

| 原来 | 现在 |
|---|---|
| 窗口页只有「额度池记的账」，自带模型的窗口一片空白 | 两笔账都摆：额度池记的 + 成员 CLI 自报的（带缓存命中） |
| 「用量」没说清是哪本账 | 写清出处：哪部分是平台记的、哪部分是成员自己报上来的 |

## 为什么

仓库主人：「抓不到缓存命中率吗」→「(a) + (c) 一起做」。

## 怎么做到不出错

1. 解析不出来就是「没有数据」，不写 0。
2. 两笔账分开：额度池的用量账只算额度池；成员自报的另算，不混。
3. 不碰在途文件（H-8 已查）。

## 完成标准

1. 自带模型的窗口也有用量与缓存命中率（真数据里 Claude Code 那次有 1,182,464 缓存读）。
2. 界面上写清两笔账的出处。
3. 办公室全部冒烟无回归；构建通过；`./scripts.sh check` 全绿。
4. `docs/traceability.md` 登记 `MVP-UI-041`。

## 验证结果

| 门禁 | 结果 |
|---|---|
| 后端新增单测 | `backend/tests/test_cli_reported_usage.py` 9 项全过（三种形状 / 重复行不重复计 / 认不出是 None / 全 0 不记 / 两笔账分开 / 提交结果后账上多一笔） |
| 真跑（8849 真平台 + 4174 预览） | Claude Code 窗口：「成员自报：消耗总 token 2,839,011（输入 2,759,660 · 输出 79,351 · 命中缓存 2,642,176）」「成员自报：缓存命中率 96%」；Codex CLI 窗口：「额度池：消耗总 token 36（输入 15 · 输出 21 · 命中缓存 0）」「额度池：缓存命中率 0%」；OpenCode 窗口：成员自报 60,920,848（命中缓存 59,877,096）· 命中率 99%；页面无报错 |
| 办公室全部冒烟 | ok（30/33；3 条恒失败是环境依赖：Godot dev server、本机 ollama `qwen3:4b`、在册凭据窗口 7≠9） |
| `office/` 构建 | ok（main 213.23 kB JS） |
| `tools/docstate` / `tools/discipline` / `ruff` | 98/98、44/44、ruff 全绿（`ruff check` 与 `ruff format --check` 两条） |
| `pytest`（全量） | `PYTEST_EXIT=0` |
