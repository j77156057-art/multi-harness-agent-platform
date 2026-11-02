# 人面动作对账：界面上还剩哪些「该人做、但没人做」的事

状态：2026-11-02 对账完成；两个真缺口都已闭环（§3 撤销分配、§4 人审打回）。作者：助理 agent。

## 0. 为什么写这个

办公室是**给人看的观察面**。所以"后端做了但界面没有"不能按端点数算 —— 后端有 114 个
handler，其中大多数是**执行方**（窗口自己领活、上报、合并）与**管理员窗口**（拆解、派单、
传阅、开轮）的接口，界面本来就不该替它们做。真正要问的是：

> 权限矩阵里「**只允许人**」的那些动作，人现在能不能在这个界面上做完？

## 1. 怎么判（机械对账，不靠印象）

三个来源交叉：

1. `backend/app/auth/policy.py` 的 `PERMISSIONS`：筛出 `frozenset({HUMAN})` 的键（29 个）；
2. `backend/app/api/routes.py`：每个 handler 的路径，以及它声明的权限键（`_guard("x:y")`）；
3. `office/server/formal-dispatch-gateway.mjs`：它转发到后端的路径（`call('...')`）、
   它自己认的路由（`route==='...'`），以及前端 `office/src/*.js` 有没有调用方。

一个动作要算"人能在界面上做"，三样缺一不可：**后端有 → 网关转发 → 前端有按钮**。

复核命令（三条 grep 就够，路径按需改）：

```powershell
# 只给人的权限键
Select-String -Path backend\app\auth\policy.py -Pattern 'frozenset\(\{HUMAN\}\)'
# 网关有没有这条转发
Select-String -Path office\server\formal-dispatch-gateway.mjs -Pattern '/revoke|/review/claim|/circulation'
# 前端有没有调用方
Select-String -Path office\src\*.js -Pattern 'review/claim|/revoke|same_provider'
```

## 2. 结论表

| 人面动作（权限键） | 后端 | 网关 | 界面 | 判定 |
|---|---|---|---|---|
| 建项目 / 建群（`project:create`、`group:create`） | 有 | 有 | 有 | 已上 |
| 改名 / 移除 / 找回（复用 `project:create`） | 有 | 有 | 有 | 已上 |
| 批准 / 驳回方案（`plan:approve`、`plan:reject`） | 有 | 有 | 有（`admin-agent.js` 的「批准，开始做」） | 已上 |
| 合并确认，手抄要合的 commit（`merge:confirm`） | 有 | 有 | 有（`/human/merge`） | 已上 |
| 恢复 / 重开 / 释放 / 取消（`task:resume|reopen|release_escalated|cancel`） | 有 | 有（`/human/*`） | 有 | 已上 |
| 受理升级、问人（`task:adjudicate`） | 有 | 有 | 有（`human-actions.js`） | 已上 |
| 预算用完再给一次机会（`task:attempt_budget_override`） | 有 | 有 | 有 | 已上 |
| 吊销一条窗口凭据（`harness:credential`） | 有 | 有 | 有（凭据面板上的「吊销」） | 已上 |
| 传阅开轮/关闭/修订/完成（`circulation:*`） | 有 | 无 | 无 | **不做**：R10 §39 与 `OFFICE-UI-01` 把它划给管理员面，管理员 worker 真的在跑这几步（`admin/worker.py` 的 `start_circulation`） |
| 分配开轮 / 冻结（`allocation:open|close`） | 有 | 只读列表 | 只读 | **不做**：同上，轮次由管理员 worker 开（`pending_allocation`） |
| 分配裁决 / 暂缓（`allocation:decide`） | 有 | 有 | 有 | 已上（人也能改管理员给的结果） |
| **撤销一份尚未 Claim 的分配**（`assignment:revoke`） | 有（`POST /revoke`） | 有（`/allocation/revoke`、`/allocation/assignment`） | 有（任务详情页「这条活定给了谁」） | **已做**，见 §3 |
| **人审：对「已通过」的打回**（`task:reopen` → `reopen_for_changes`） | 有（`POST /human/reopen_for_changes`） | 有（`/human/reopen_for_changes`） | 有（approved 任务详情页「打回返工」） | **已做**，见 §4 |
| 人**主动**认领审阅（`review:human_claim`、`review:human_submit`） | 有 | 有（`as:1` 即人） | 无 | **不需要做**：口径是"管理员先审、人后审"，人的位置是打回，不是在 `awaiting_review` 抢审阅 |
| **同厂商审阅豁免**（`review:same_provider`） | 有（`POST /human/review_same_provider`） | 无 | 只显示结果 | 仍只在 CLI：它服务于"让同厂商的 **harness** 来审"，与人审无关 |

另：`pool:user_keys`、`relay:letter`、`wording:read` 不在 `api/routes.py` 里（各自模块有自己的
路由文件），界面上都已经有入口，不在这张表里重复。

## 3. 已做：撤销一份还没被接走的分配

**缺口**：裁决之后，`allocations` 里那一行就定死了（`UNIQUE(round_id, task_id)` 一轮一任务一条
裁决）。要是裁决给错了人、或者那人一直没接，**界面上没有任何回退手段** —— 只能去 CLI 或直接
打 HTTP 调 `POST /revoke`。而后端这条边早就做完了（`assignment:revoke`，`allocations.state`
只允许撤销尚未 `claimed_at` 的那一份），缺的只是两层皮。

**为什么值得做**：它是"裁决"这条路上唯一的**纠错**动作。人点了裁决之后没有退路，等于把一个
需要人判断的动作做成单向门 —— 这正是"门槛 + 让人自愈"要避免的形状。

**2026-11-02 已实现**（三处，都在 `office/`）：

1. `GET /api/formal/allocation/assignment?task_id=…` —— 这条活现在定给了谁，并算出能不能撤。
   为什么不复用 `/allocation/rounds`：那个读面默认只看**开着的轮**，而裁决过以后轮就是
   `decided`，恰好在最需要撤销的时候一条都查不到。
2. `POST /api/formal/allocation/revoke` —— 只收 `task_id` + `reason`（理由**必填**）。**不收**
   `expected_revision`：那是乐观锁入参，界面手里的快照可能是几十秒前的，拿旧值去撞只会让人看到
   "修订号不对"；撤销是危险动作，正确做法是**写之前现读一次现状**，用当下的修订号提交。真正的
   闸门（已 Claim 不许撤、轮次状态）仍在平台那边判，网关不复制它的规则，只挡明显撤不掉的。
3. 任务详情页多一块「这条活定给了谁」：谁、什么状态、开没开工；能撤时给一句理由输入框 +
   **两步**确认（第一次点只是把按钮换成「确认撤销」，第二次才发）。已经开工的不给按钮，直接说
   "要收口请用取消或释放"。

**验过的**：

- 网关单测 4 条（`node --test server/formal-gateway-admin.test.mjs`，15/15 含既有的 11 条）：
  未接走的可撤且**用现读的修订号**；已开工的 `409 assignment_not_revocable` 且**一次写入都没发**；
  没理由的 `400` 且不发；没有分配的 `409 assignment_not_found`。
- 界面回归 `office/scripts/verify-office-allocation-revoke.mjs`（桩数据）：块里写清定给了谁、
  没理由不发请求、两步确认、发的是 `POST /allocation/revoke` 带理由、平台拒绝时原话回灌且按钮
  退回未确认、已开工没有按钮。
- 真平台四条（`X-Office-Group: collab-big-g1`）：读面 `revocable=false / claimed=true`；
  没理由 `400 allocation_reason_required`；已开工 `409 assignment_not_revocable`；
  跨群 `404 task_not_in_group`；不存在的任务 `404 resource.not_found`。
- **没验到的**：真实的"撤掉一份还没接走的分配"happy path —— 这台机器的库里 33 份分配全部已
  `claimed`，没有可撤的对象；要造一个得先跑完 方案→传阅→开轮→申请→裁决 整条链。这条目前只有
  后端 3 条测试 + 网关/界面桩数据覆盖。

## 4. 已做：人审 = 对「已通过」的一次打回（默认通过）

**口径**（2026-11-02 拍板）：管理员先审，审过就是放行；人手里留一个**不同意就写原因打回**的
权力。所以人**不需要**在 `awaiting_review` 阶段认领审阅，也**不需要**人审租约 —— 他的动作是
针对一条已经通过的活的即时否决。

**这条边规范里早就有**（§10 第 462 行）：

```
approved | reopen_for_changes | human.adjudicate 退回或检测到 approved 后分支头前移 | changes_requested
```

状态机也实现了它（`backend/app/state/machine.py`，带 `branch_unfrozen`），但此前**没有任何服务
能触发**：全仓只有状态机与文案表提到 `REOPEN_FOR_CHANGES`，`human/service.py` 的 `reopen` 只认
`abandoned / awaiting_human / 两种 drift`。也就是说这条路画好了却没通电 —— 缺的不是设计，是接线。

**2026-11-02 接上了**（三处）：

1. 后端 `HumanOpsService.reopen_for_changes` + `POST /human/reopen_for_changes`
   （权限复用 `task:reopen`，不新开权限边）：只允许 `approved`，理由**必填**，
   事件 `reopen_for_changes` 的 payload 里记 `reason` / `decided_by` / `from` ——
   半年后有人问"这版都过了为什么又返工"，答案在事件流里。
2. 网关 `/human/reopen_for_changes`：理由必填（空理由 `400 human_reason_required`，
   且**不发**请求），身份用 operator；只让 `task_id` / `reason` 两个字段进来。
3. 界面：`approved` 的任务详情页多一块「管理员审过了，这一版默认放行」，写明**不必再点同意**，
   只有一颗「打回返工」按钮 + 理由输入 + 两步确认；打回成功后刷新任务表，那块随之消失。

**为什么不需要人审租约**：租约是给 harness 认领制用的（认领 20 分钟、过期由 `sweep_review_lease`
标 `expired`、任务时钟继续走）。人这个动作不认领、即时完成、只有一个 operator，不存在"谁占着"。
**为什么不需要同厂商豁免**：`_assert_review_policy` 里 `reviewer_kind == 'human'` 直接放行、
不查厂商；`review:same_provider` 那条豁免是给"让一个与执行方同厂商的 **harness** 来审"的，
和人审是两件事。（我上一轮把这两条绑在一起说"上界面前得先想清楚"，那是错的。）

**仍然只在 CLI / 需另开会话的**：让**成员窗口**走同厂商审阅（`POST /human/review_same_provider`）。
它是安全边（允许"自己人审自己人"、每任务只能豁免一次），上界面要先解决"为什么给"的记录面 ——
现在落在 `task_decisions` 的 `scope='review_same_provider'`，界面上只显示结果、不给按钮。

**验过的**：后端 5 条（`tests/test_human_reopen_for_changes.py`：打回落到 `changes_requested`、
理由进事件、从哪个状态来的也记、理由必填且拒绝时不动状态、非 `approved` 明确拒绝、未知任务）；
网关 2 条（理由必填且不发、字段不许夹带）；界面 1 条桩数据回归
（`office/scripts/verify-office-approved-return.mjs`：只有一颗按钮、没理由不发、两步确认、
发的是 `POST /human/reopen_for_changes`、打回后那块消失）。

## 5. 本版不做什么

- 不把传阅、开轮、计划细化搬上界面：它们归管理员面，搬上去等于让两个人抢同一件事；
- 不给执行方侧接口（`/claim` 那一族、`/submit_result`、`/merge`、`/heartbeat`）开界面入口；
- 不恢复「人工派单」路由（`formal-dispatch-gateway.mjs` 里那段注释写清了原因）。

## 6. 这一轮的附带产出

`office/scripts/verify-project-workspace.mjs` 重写过：它断言的 `#phoneDialog` / `#returnProjectsBtn`
/ `data-project-phase=phone` 那套「手机开机」界面**早就删掉了**（建完项目直接进开工向导），
却因为属于「没起界面就跳过」的 24 个脚本而一直没人发现。现在改成按当前界面断言（大厅 →
建项目 → 开工向导 → 回大厅 → 卡片 +1 → 刷新还在），并去掉了"大厅必须是空的""成员恰好 9 个"
这两个环境假设。对着 4175 真跑：`{"checks":21,"result":"PASS"}`。
