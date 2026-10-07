# 认领：split-closure

<!-- 本文件由 Qoder 创建。其他 AI 请勿修改本文件；改你自己的即可，永不冲突。 -->

| 字段 | 值 |
|---|---|
| task_id | `split-closure` |
| Harness | Qoder |
| 认领时间 | 2026-10-07 22:53 |
| 分支 | `agent/local/qoder/split-closure/a1` |
| 状态 | ✅ done（实现 27d7be0；待合入，外部验收待认） |

任务表对应行：`TASKS.md` → 「任务表」章节，搜 `split-closure`。

## 变更记录

- 2026-10-07 22:53：认领。协调者选定「(a) split 闭环并卡（子任务生命周期 +
  all_children_merged 巡检，让 split 真能跑完）」。前置：adapter-verbs（`0bce533`）
  已把 split_task 转正，但子任务为惰性 draft、唤醒生产者为空。侦察中发现两个
  必须同批处理的真问题：①`task_dependencies` 方向契约——R10 §10 L587 规定
  parent=等待方，而 `_materialise_task` 写反且零测试覆盖；②既有巡检会误杀
  split 父任务（stuck 指纹不含子任务活动 → 3 个巡检周期即升级；blocked_sla
  60 分钟无条件升级）。
- 2026-10-07 23:26：完成。实现 `27d7be0`；全量门禁绿（docstate 90/0、
  discipline 44/0、ruff 97 files、pytest 381 passed 净增 7）。两个新守卫均经
  变异验证（反向修改必红）。`CORE-SPLIT-001` 升 🟡（upstream_invalidated_seq
  仍归 MERGE-REVERT-002）。

