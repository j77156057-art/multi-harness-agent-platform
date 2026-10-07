# 认领：budget-guard

<!-- 本文件由 Qoder 创建。其他 AI 请勿修改本文件；改你自己的即可，永不冲突。 -->

| 字段 | 值 |
|---|---|
| task_id | `budget-guard` |
| Harness | Qoder |
| 认领时间 | 2026-10-08 03:25 |
| 分支 | `agent/local/qoder/budget-guard/a1` |
| 状态 | ✅ done（实现 ed00e94；待合入，外部验收待认） |

任务表对应行：`TASKS.md` → 「任务表」章节，搜 `budget-guard`。

## 变更记录

- 2026-10-08 03:25：认领。吸收队列 ③（协调者「开3」）。机制学习：Paperclip
  `server/src/services/budget-reservations.ts`（调度前预留：锁内检查
  committed=observed+held、`committed>=limit || committed+amount>limit` 即拒、
  零额行也拦重复派发）、`run-cost-accounting.ts`（结算：held→settled/released，
  "未证实不落账"）、`budget_policies`/`budget_incidents` schema（阈值/事故/
  解决动作）——本会话已逐行核读，代码原生重写。原生落点对齐 R10：
  任务级 `budget_tokens`（§8 示例字段、§9 校验器已实现）、split「继承未使用
  预算」（L679，split-closure 曾声明该 guard 无模型可评）、「超预算进入
  escalated」（L1146）——本卡给出运行期预算模型与调度前预留的执行点。
- 2026-10-08 04:22：完成。实现 `ed00e94`；全量门禁绿（docstate 90/0、
  discipline 44/0、ruff 116 files、pytest 436 passed，净增 20）。两项变异
  验证（拆 reconcile → 滞留断言红；拆准入谓词 → 拒绝断言红）。巡检测试的
  时钟缺陷（import 钟 vs 租约的 claim 钟）在门禁全量跑中暴露并修掉。
  声明缺口：reclaim/take_over 路径不预留（保救援路径）；事故阈值=observed
  口径（在途预留不制造事故）。
