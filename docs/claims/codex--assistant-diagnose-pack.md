# 认领：assistant-diagnose-pack

| 字段 | 值 |
|---|---|
| task_id | `assistant-diagnose-pack` |
| Harness | Codex |
| 认领时间 | 2026-10-23 19:36 +08:00（仓库主人指示"继续完善 agent 搭建"；守门因 tasks/service.py 共享在途未收敛，按 roadmap 先做证据束取样器） |
| 分支 | `agent/local/codex/OFFICE-UI-01/a1` |
| 状态 | done（取证束＋诊断姿势＋scripted 全绿入仓 `17914d5`；全量 pytest 绿，见验收结果） |

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

## 验收结果（2026-10-23）

- scripted：`tests/test_assistant_loop.py` 全绿（task.diagnose 用例：诊断束
  四读齐、失败事件"最新至多 3 条"过滤、output/verification 诚实透传、
  非标识符拒收、白名单闭合＋say 唯一写、诊断姿势条目在案）。
- 实现入仓：commit `17914d5`；test_id 沿 `MVP-ADAPTER-003`（未自造 ID）。
- 全量 pytest（最终树，含 `1c65241`）：1027 用例全点 `.`，进度
  `[100%]`，零 F/E/s/x、无 short test summary info；slowest durations 表
  在案（输出留档 `D:\Temp\opencode\full_pytest_final.txt`；末行"N passed"
  因 Start-Process 重定向缓冲伪影未落档，用例级证据完整）。
- ruff check＋format 全仓绿；docstate 98 项 0 失败；discipline 44 项
  0 失败（1 告警为既有根工作区占用提示）。
- 真平台首跑（真问题→免费档轮次→私聊回复→trace 落账）另记于 P2 卡
  "首跑真平台验证"节；task.diagnose 只触任务读面（不涉消息形状），与本轮
  worker 修复互不影响。
