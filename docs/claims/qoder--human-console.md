# 认领：human-console

<!-- 本文件由 Qoder 创建。其他 AI 请勿修改本文件；改你自己的即可，永不冲突。 -->

| 字段 | 值 |
|---|---|
| task_id | `human-console` |
| Harness | Qoder |
| 认领时间 | 2026-10-08 13:30 |
| 分支 | `agent/local/qoder/human-console/a1` |
| 状态 | 🚧 doing |

任务表对应行：`TASKS.md` → 「任务表」章节，搜 `human-console`。

## 变更记录

- 2026-10-08 13:30：认领（协调者「根据后端实际能力做出对应的前端」）。现状：
  前端只有只读面（rail/群聊/线程/diff/发消息）；后端已存在、但人在界面上够
  不着的两片能力——①管理面：`POST /projects`、`/projects/{id}/groups`、
  `/groups/{id}/plans`、`/plans/{id}/approve|reject`；②§18 人工决策面：
  `/human/resume|reopen|release|adjudicate|cancel`。本卡把这两片接进三栏壳
  （R10 §22：左栏建项目/群组，中栏「方案」= 计划与审批，右栏线程内决策条）。
  **零后端改动**：只消费既有端点；后端没有的读面（plans 列表、harness 成员
  列表、task_decisions 读）如实留缺，不发明 UI。
