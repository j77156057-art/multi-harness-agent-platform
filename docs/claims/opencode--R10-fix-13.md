# 认领：R10-fix-13（方案传阅与分配仲裁）

<!-- OpenCode 独占。其他 AI 请勿修改本文件；改你自己的即可，永不冲突。 -->

| 字段 | 值 |
|---|---|
| task_id | `R10-fix-13` |
| Harness | OpenCode |
| 认领时间 | 2026-10-17 16:10 |
| 分支 | `agent/local/codex/FORMAL-MULTI-HARNESS-01/a1`（见下方「分支说明」） |
| 状态 | doing：Stage A 规范登记 + Stage B 服务/状态机/路由/权限已完成，自测全绿；待 Merge Gate |

任务表对应行：`TASKS.md` → 「任务表」章节，搜 `R10-fix-13`。

## 分支说明（如实记录，不美化）

本切片的改动目前**挂在 codex 的 `FORMAL-MULTI-HARNESS-01/a1` 工作区**
（`.worktrees/codex/`）里，尚未单独切 `agent/local/opencode/R10-fix-13/a1` 分支、尚未提交。

为什么不装作已经切好：同一工作区里同时躺着三块**互不相同**的改动 ——
(a) 群主授权的平台缺陷修复（`review_claim` 死锁、findings 闭环、scope 门禁、
`default_runner` 环境、verify 解释器钉版），(b) 正式分派管理员闭环
（FORMAL-MULTI-HARNESS-02），(c) 本切片（R10-fix-13）。三者的授权来源、
验收口径都不同，硬拆成一次提交会让「哪条改动被谁授权、按什么验收」
混在一起。切分支与拆分提交留给本切片结卡时做。

## 修什么

群主 2026-10-17 决定：`D:/w/office-preview/handoff/TASK-ALLOCATION-ARBITRATION.md` §7
的**分配/传阅后端真做**，不走「用现有机制近似」的路线。产品流程的规范出处是
`handoff/START-HERE.md` §2（需求 → 管理员拆解 → 成员传阅评论 → 管理员整理 →
认领/协助/完成/独立审查；默认两轮传阅）与 `handoff/dispatch-2026-10-11/PLAN-CONTRACT.md`。

该文件 §117 明确：**新事件名、状态和 schema 经规范复审登记后再落代码**；§128 明确
当前权限矩阵只有 `human`/`harness` 两种主体，管理员身份需要显式委托。

## 分两阶段（这也是为什么要先登记再写码）

**Stage A（规范登记，必须与解析器同一次提交）**

- §37.7：新增**第四个 allowlist 类别 A（分配事件，18 项）**，导语三类 → 四类，
  并集 89 → 107，封闭规则 1 扩为四集合、封闭规则 3 增 §39→A 映射。
  为什么不塞进 O（观测）：`allocation.decision_made` **会授权一个 attempt 开工**，
  而 O 的定义是「由巡检器、安全扫描或幂等层写出，不驱动任务状态迁移」——
  按观测记就是伪造语义。
- 新增 **§39 分配与传阅**：两台状态机，与 A 集合双向封闭。
- §8 追加 8 个实体；§17 追加权限行；§29.3/§29.6 计数同步；§38.11 修复记录。
- `tools/docstate/docstate.py`：`ALLOWLIST_HEADINGS` 增 A、`EXPECTED_COUNTS` 增 A 与
  并集 107、新增 `[allocation]` 组（§39↔A 双向封闭）。
- `backend/app/spec.py`、`backend/app/events/store.py`（allowlist 解析块数 3 → 4）、
  `backend/tests/test_spec_mirror.py`。

**Stage B（迁移 + 状态机 + 服务 + 路由 + 权限）**

- `backend/migrations/016_allocation.sql`（8 张表）
- `backend/app/allocation/machine.py`（§39 两台状态机的代码镜像，22 条边）
- `backend/app/allocation/service.py`（传阅 6 个动作 + 分配 11 个动作 + Claim 授权查询）
- `backend/app/api/routes.py` / `schemas.py`（20 条路由）
- `backend/app/auth/policy.py`（15 个新键）
- 测试 44 项：`test_allocation_machine.py` / `test_allocation_circulation.py`（12）/
  `test_allocation_service.py`（24）/ `test_allocation_routes.py`（8）

## 写服务时抓到、并已修掉的真 bug（这是本切片最值得看的部分）

三条都**不是**笔误，而是「照正文抄一遍才会暴露」的结构问题：

1. **第二轮传阅谁都提交不了**。`circulation_reviews` 的唯一键是
   `(circulation_id, harness_id)`，而 §39 的 `revising → circulating` **复用同一条**
   传阅记录 —— 第一轮评过的人在第二轮被唯一键挡住 → 传阅永远到不了 `completed`
   → 硬门禁反过来把整个流程锁死。修法：唯一键带 `round_no`，`revise` 让轮号前进。
2. **一张分配轮只能分配一个任务**。有一次裁决就进 `decided`，而
   `allocation.decision_made` 只登记了 `closed → decided` → 同轮第二个任务被状态机拒绝。
   §10 处理「同一事件重复发生」用的是自环（已有 7 条），§39 缺了同款。修法：正文与镜像
   各补 `decided` 上的自环（`decision_made` / `decision_deferred` / `material_requested`）。
3. **没人申请的任务发不出邀请**。`assignment.invited` 只从 `decided` 出发，
   而「冻结之后才发现没人申请」时轮次还在 `closed` → 邀请被拒。
   修法：正文写成 `closed/decided | assignment.invited | ... | decided`。

另有一处**实现与已登记正文对不上**：§38.11 把申请状态写成 `submitted/frozen/withdrawn`，
第一版服务只有 `submitted/withdrawn`。修法是让实现跟上正文（`close_allocation_round`
把仍在候选集合里的申请转 `frozen`）。

## 顺带修掉的一处门禁红灯

Stage A 让 docstate 断言数 90 → 98，而 `README.md` / `docs/dev-convention.md` 里仍写着 90。
`tools/discipline` 的 `[counts]` 组**专门**防这件事（「加了断言忘了改文档，文档就开始骗人」），
于是 discipline 转红并连带 3 个 `test_gate_utf8` 失败。已把两处声明改成 98。

## `claim:authorized` 的强制口径（群主 2026-10-17 裁定：分群开关）

§39 硬规则 2 的字面写法是「只有已 `accepted` 的分配才构成 §11 Claim 的授权」。严格实现
会让现存 57 条任务全部不能再被领取、三条已跑通的合并链与 `test_real_adapters` 全红 ——
那不是把规则做实，是换掉领取机制。群主选**分群开关**，已实现：

- `AP_ALLOCATION_REQUIRED_GROUPS`（逗号分隔，默认空）：列在里面的群，**第一份** attempt
  必须有一份「已 `accepted` 且尚未用掉」的分配作授权，且分配轮绑定的快照必须等于任务
  当前的快照（硬规则 2 的「计划版本」一格），否则以 `claim.not_authorized` 拒绝。
- 返工（`changes_requested`）不走这道门：它继续的是同一份已被授权的作业；若返工也要新
  分配，一次 verify 失败就会把任务钉在等分配上。
- 授权在真正开出 attempt 时才记账（`claimed_at`/`claim_id`，与 `task_claims.claim_id`
  同值），所以「排队等资源」不消耗授权，而「撤销一份没人用的授权」与「撤掉一个正在跑的
  Lease」可以 join 分开判断。
- **尚未强制**：`expected_revision`（分配版本）。§11 的 Claim 请求里没有这个字段，
  强制它要给 `/adapter/claim` 加字段并让所有调用方带上。登记为待办，不假装已覆盖。

## 验证方式

~~~bash
cd backend
../.venv/Scripts/python.exe -B -m pytest tests/test_allocation_machine.py \
  tests/test_allocation_circulation.py tests/test_allocation_service.py \
  tests/test_allocation_routes.py tests/test_spec_mirror.py -q
cd ..
.venv/Scripts/python.exe -B tools/docstate/docstate.py --spec docs/spec/0.3-R10.md  # 98 项 0 失败
.venv/Scripts/python.exe -B tools/discipline/discipline.py                          # 全绿
~~~

## 变更记录

- 2026-10-17：认领（群主 2026-10-17 决定真做分配/传阅后端）。
- 2026-10-17：Stage A + Stage B 完成，44 项分配测试绿，docstate 98 项 0 失败。
- 2026-10-17：群主裁定 `claim:authorized` 走**分群开关**；门禁实现并补 14 项测试
  （`tests/test_claim_allocation_gate.py`），`claim.not_authorized` 登记进错误码表，
  §39 硬规则 2 增补强制范围。同轮把仓库根口径的 ruff 清干净（此前我只按文件跑，
  漏了 7 条与 15 个格式漂移）。
