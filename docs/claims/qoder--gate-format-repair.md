# 认领：gate-format-repair

<!-- 本文件由 Qoder 创建。其他 AI 请勿修改本文件；改你自己的即可，永不冲突。 -->

| 字段 | 值 |
|---|---|
| task_id | `gate-format-repair` |
| Harness | Qoder |
| 认领时间 | 2026-10-07 20:43 |
| 分支 | `agent/local/qoder/gate-format-repair/a1` |
| 状态 | 🟢 doing |

任务表对应行：`TASKS.md` → 「任务表」章节，搜 `gate-format-repair`。

## 变更记录

- 2026-10-07 20:43：认领。协调者指令：先把当前仓库收尾、`./scripts.sh check`
  全绿之后才叠加新架构。实测 main（`a68be60`）门禁红点在 **lint**：
  `ruff format --check` 拒收 `backend/tests/test_gate_utf8.py` 与
  `backend/tests/test_merge_guard.py` —— win-utf8-stdio（`0ed6e0d`）合入时这两个
  文件未过 formatter；mergeguard 自身的门禁只跑 docstate+discipline，
  lint/pytest 不在其内，所以基线红没能拦在合并前；`set -e` 又让 `check` 在 lint
  处短路，pytest 都轮不到跑。本任务：①机械格式化这 2 个文件；②回填 5 张已完成
  卡的标题 in-flight 标记（正文与任务行均已 done，仅 `###` 标题滞后）。
