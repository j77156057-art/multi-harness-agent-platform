# 认领：rules-tighten

<!-- 本文件由 Qoder 创建。其他 AI 请勿修改本文件；改你自己的即可，永不冲突。 -->

| 字段 | 值 |
|---|---|
| task_id | `rules-tighten` |
| Harness | Qoder |
| 认领时间 | 2026-10-09 01:05 |
| 分支 | `agent/local/qoder/rules-tighten/a1` |
| 状态 | 🚧 doing |

任务表对应行：`TASKS.md` → 「任务表」章节，搜 `rules-tighten`。

## 变更记录

- 2026-10-09 01:05：认领（协调者路线图第 4 项「协作规则收紧」）。清账 drill
  F5/F7/F11 三条：①`review_policy=cross_provider` 只存字段不强制——在
  review.claim/submit 处按注册表 provider 判同源；②worktree 无回收（§14
  "归档校验通过后才回收"，§10 失败边"冻结 attempt、归档旧 worktree"）——
  新增 `worktree_recycle` 巡检（第 15 员），复用
  `archive_and_recycle`（bundle→verify→删除）；③验收"存在性"判据可被空产出
  通过——VerifyWorker 对"与基线无差异"的产出不再放行（receipt 标
  empty_diff，仍是冻结四值里的 code_failed）。
