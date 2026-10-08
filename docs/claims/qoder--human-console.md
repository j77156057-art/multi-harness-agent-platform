# 认领：human-console

<!-- 本文件由 Qoder 创建。其他 AI 请勿修改本文件；改你自己的即可，永不冲突。 -->

| 字段 | 值 |
|---|---|
| task_id | `human-console` |
| Harness | Qoder |
| 认领时间 | 2026-10-08 13:30 |
| 分支 | `agent/local/qoder/human-console/a1` |
| 状态 | ✅ done（实现 c771867；待合入） |

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
- 2026-10-08 13:47：实现完成并提交 `c771867`（api/store/三组件/Shell/style
  + 测试扩至 46 passed；前端四件全绿）。浏览器实测：独立 8892 实例 + vite
  5185，管理链 5/5、resume/reopen/cancel 真跑（escalated 两动词如实标注
  未真跑，vitest+契约覆盖）；独立 API 复核与决策台账留档
  （backend/data/hc-browser-evidence.md，gitignored）。状态 → done。
