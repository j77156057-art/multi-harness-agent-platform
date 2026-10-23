# 认领：assistant-diagnose-pack

| 字段 | 值 |
|---|---|
| task_id | `assistant-diagnose-pack` |
| Harness | Codex |
| 认领时间 | 2026-10-23 19:36 +08:00（仓库主人指示"继续完善 agent 搭建"；守门因 tasks/service.py 共享在途未收敛，按 roadmap 先做证据束取样器） |
| 分支 | `agent/local/codex/OFFICE-UI-01/a1` |
| 状态 | doing（取证束打包＋诊断姿势 prompt＋scripted 测试） |

## 做什么（路线图"证据束取样器（Diagnostic）"落地）

1. `task.diagnose` 只读工具：一次取准一个任务的失败证据束——任务状态/
   当前 attempt、`/events` 里 abandon/release 类失败事件（最新至多 3 条，
   payload 原样保留）、`/output` 的 CLI stdout/stderr 尾（拿不到如实
   `None`）、验证回执；只组读面已公开的事实，不猜、不裁决。
2. prompt 补"诊断姿势"：问"为什么这单失败"时摆证据不编因果；结论挂证据
   引用；改派与否写"待管理员裁决"。
3. 白名单闭合照旧：say 仍是唯一写。

## 边界

- 全程不碰 `backend/app/tasks/service.py`（守门落点，FORMAL-MULTI-HARNESS-02
  共享在途）、`backend/app/api/routes.py`（R10-fix-13 在途）、
  `backend/app/admin/**`、`backend/app/auth/**`、migrations；
- 不新增表/事件/路由/权限键；结构化 failure.json 磁盘束不另行开面，
  诊断束=读面已公开事实的机械拼盘；
- 全 scripted 不烧真额度。

## 完成标准

scripted：诊断束四读齐且路径正确；失败事件过滤与"最新至多 3 条"；output
/verification 缺失诚实透传；非标识符 task_id 拒收；白名单闭合（含新工具）
＋say 唯一写；诊断姿势与自检条目在案。ruff、docstate、discipline、
pytest 定点＋全量绿。
