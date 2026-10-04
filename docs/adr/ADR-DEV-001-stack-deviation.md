# ADR-DEV-001：MVP 技术栈偏离 R10 §5

状态：**已接受**（MVP 阶段）
日期：2026-10-05
关联任务：MVP-01
影响范围：`backend/**`、`frontend/**`、`tools/**`

---

## 背景

R10 §5「系统结构」给出的推荐技术栈是：

| 层 | R10 推荐 |
|---|---|
| 后端 | Go |
| 前端 | React、TypeScript、Vite |
| 数据库 | SQLite、WAL、busy_timeout |
| 实时事件 | WebSocket 只读订阅 |

R10 §23 选 Go 的理由是「使用纯 Go SQLite 驱动减少 Windows cgo 问题」。

本项目实际决定使用 **Python FastAPI + Vue3**。这是对 R10 §5 的**显式偏离**，
按 H-3 必须记录，不能默默改。

## 决策

MVP 阶段使用 Python 3.11+ / FastAPI / SQLite（标准库 `sqlite3`）/ Vue3 / TypeScript / Vite。

## 理由

### 1. MVP 的验证目标与语言无关

R10 §0 界定的MVP 目标是三条协调语义：

1. 任务不会重复领取；
2. 任务状态可恢复；
3. 代码经过验证后才能合并。

这三条全部落在**事务语义与状态机正确性**上，与实现语言无关。

### 2. `sqlite3` 在 Windows 上无 cgo 依赖

R10 选 Go 的核心顾虑是 cgo。Python 标准库 `sqlite3` 是**内置扩展**，
不存在 cgo/编译链问题，且：

- `BEGIN IMMEDIATE` 直接可用（R10 §11 Claim 事务的基础）；
- `execute().rowcount` 即 `RowsAffected`（R10 条件 UPDATE 断言的基础）；
- `busy_timeout` 通过 PRAGMA 设置。

三者语义与 R10 要求**完全一致**，不打折。

### 3. 并发测试成本显著更低

R10 §26 要求 `CORE-CLAIM-002` 用「两个真实 SQLite 连接并发抢同一任务」验证。
Go 里需要手写 goroutine + channel 同步；Python 里用 `threading` + `Barrier`
即可，测试代码量约为 Go 的 1/3。

考虑到 MVP 阶段并发语义是**主要风险点**（4 条 P0 中有2 条与 Claim 幂等相关），
降低这块的测试摩擦是值得的。

### 4. 与团队现有栈一致

团队主力栈是 FastAPI + Vue3。MVP 阶段引入第二套技术栈会产生额外的
认知负担与上下文切换成本。

## 后果

### 正面

- 规范检查器 `tools/docstate` 可用纯 Python 标准库实现，**零外部依赖**，
  任何环境都能跑（这正是 R10 §29.6 要求「优先脚本复核」的前提）；
- 测试迭代快，适合 MVP 的高频改动；
- 与既有代码风格统一。

### 负面

- **偏离 R10 §5**，若未来进入生产阶段需要重新评估（Go 的单二进制部署、
  并发性能优势在多 Worker 场景更明显）；
- Python GIL 理论上限制并发，但 MVP 的并发瓶颈在**SQLite 写锁**而非 CPU，
  GIL 不构成实际瓶颈（R10 §23 建议并发 verify=3，远低于 GIL 阈值）；
- 打包体积大于 Go 单二进制。

## 回退条件

出现以下任一情况，应重新评估是否切回 Go：

1. 出现 Python 无法满足的性能指标（例如写事务 P99 > 250ms —— 注意 R10 ADR-007
   本身就把「写入等待超过 250ms」定为迁移评估触发条件）；
2. 需要部署为单二进制且无Python 运行时环境；
3. SQLite 达到 R10 ADR-007 的 20GB / 10 Worker 阈值（此时该考虑 PostgreSQL，
   而非换语言）；
4. 团队主力栈发生变化。

## 兼容性保证

本ADR **不改变 R10 的任何语义约束**。以下在两种技术栈下都必须成立：

| 约束 | 出处 |
|---|---|
| `BEGIN IMMEDIATE` 或单写者 | R10 §11 / ADR-060 |
| 条件 UPDATE + `RowsAffected == 1` 断言 | R10 §11 |
| `claim_requests` 双层幂等（请求级 + 任务项级） | R10 §8 / R9-03 |
| `tasks.attempt_no` / `hotfix` / `budget_override_by` 存在 | R10 §8 / R10-12、R10-13 |
| 事件 append-only，投影可重建 | R10 §15 |
| receipt append-only + 唯一键 | R10 §16 |
| 旧 fencing epoch 一律拒绝 | R10 §13 |

若切换到 Go，上述约束需在 Go 中重新验证一遍（尤其 `RowsAffected` 的语义差异：
Go 的 `database/sql` 在部分驱动下对 `UPDATE` 返回的 rowcount 行为需实测确认）。

## 关联

- R10 §5（被偏离的原文）
- R10 §11 / ADR-060（Claim 事务）
- R10 §23（技术栈选择的原始理由）
- ADR-007（SQLite 迁移阈值）
- `docs/dev-convention.md` H-3
