# 认领：cli-roster-enumerate

| 字段 | 值 |
|---|---|
| task_id | `cli-roster-enumerate` |
| Harness | Codex |
| 认领时间 | 2026-10-23 19:18 +08:00（仓库主人指示"探的不够，opencode 还有更多免费模型"后开工） |
| 分支 | `agent/local/codex/OFFICE-UI-01/a1` |
| 状态 | done（实装＋7 条 scripted＋全量 pytest 绿＋真发现面 9 家实勘＋哨上岗 1 条真告警） |

## 做什么

CLI 槽声明式 `enumerate`：opencode 槽声明
`{"args": ["models"], "pattern": r"^opencode/.+-free$"}`，平台跑 CLI
的清单子命令（零额度）扩出免费家族；命名规则是人声明的不是嗅探的；
TTL 600s 共享缓存；清单失败诚实跳过只留声明槽。

## 边界

- 只改 `backend/app/pool/sources.py`＋新增测试；不碰
  tasks/scheduler/alerts/assistant、不新增表/事件/路由/权限键；
- opencode 花名册已实勘并入测试 fixture（`-free` 族 9＋付费族
  openai/*、openrouter/*、1/、2/）；
- 哨部署方式（--loop 值班）不属于本卡。

## 完成标准

7 条 scripted（扩展/命名空间/失败不扩/TTL/未声明不动/过滤/坏规则）
＋真发现面实勘 9 家＋哨 `--once` 5→13 入队 1 条告警＋ruff、
docstate 98/0、discipline 44/0＋pytest 定点＋全量绿。

## 记录

- 真发现面：`opencode-cli/` 下 beam-free、deepseek-v4.1-flash-free、
  flint-40b-free、glm-5.3-flash-free、mimo-v2.6-flash-free、
  minimax-m3.1-flash-free（声明本体）、nemotron-3-ultra-free、
  nemotron-3.5-lightning-free、nemotron-3.5-super-vl-free，共 9 家；
- 哨上岗：快照指纹 `e349cd68…`（5 家）→ `f844659e…`（13 家），
  告警恰好 1 条入队；
- 全量 pytest（codex 工作树）：`[100%]` 全点、退出码 0，输出留档
  `D:\Temp\opencode\p3_full_pytest.txt`（末行统计被 PowerShell
  重定向吞掉，凭退出码与 100% 判绿）。
