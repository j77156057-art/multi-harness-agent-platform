# 认领：OFFICE-UI-38

| 字段 | 值 |
|---|---|
| task_id | `OFFICE-UI-38` |
| Harness | Mavis |
| 认领时间 | 2026-10-26 05:40 +08:00 |
| 分支 | `agent/local/mavis/OFFICE-UI-38/a1` |
| 状态 | doing |

目标：**接手 codex 的在途**，把 `diff-uninitialized-repo` 这张卡收尾。

## 接手时的实况

codex 的这张卡写着「doing（实现与定点测试已就位；待合并门禁）」，提交 `59f9f45`
（那条单独提交不在任何分支上）。核实下来**实现已在 main 上**：

- `CanonicalRepo.has_object`：git 进不起（OSError）→ False ✅
- `SchedulerService.diff_for_task`：仓库未就位（`is_initialized()` 为假）→
  `reason = "canonical repository is not initialized"`，**不 500** ✅

所以这张卡该结 —— 但同样不凭「代码在」就写已验收：这一张做**逐条核实 + 真跑**。

## 范围

- `docs/tasks/notes/diff-uninitialized-repo.md`（状态与验证结果）
- `docs/claims/codex--diff-uninitialized-repo.md`（补记核实结论）
- `TASKS.md`（那张卡的状态行）
- 必要时 `backend/tests/**`（若核实发现缺口才补测试）

## 怎么做到不出错

1. **不碰实现**：代码已在 main 上；核实出缺口就另开卡。
2. 真跑要**真的把 `AP_CANONICAL_REPO` 指到不存在的目录**，看端点回什么 —— 不靠读代码
   猜。

## 完成标准

1. 逐条核实完成标准 + 真跑（端点 200、`available:false`、reason 如实）。
2. 状态改成 done。
3. 门禁全绿；`docs/traceability.md` 登记 `MVP-UI-002` 的收尾。
