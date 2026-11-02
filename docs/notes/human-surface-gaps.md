# 人面动作对账：界面上还剩哪些「该人做、但没人做」的事

状态：2026-11-02 对账完成；两个真缺口已定位，其中一个已做完（§3）。作者：助理 agent。

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
| **人审阅**（`review:human_claim`、`review:human_submit`） | 有 | **有**（`/review/claim`、`/review/submit`，`as:1` 即人） | **无** | **建议先不做**，见 §4 |
| **同厂商审阅豁免**（`review:same_provider`） | 有（`POST /human/review_same_provider`） | **无** | 只显示结果 | 与人审阅同一条线，一起判 |

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

## 4. 建议先不做：人审阅

**缺口是实的**：`/review/claim`、`/review/submit` 网关已经有（`as: 1` 就是人审），`same_provider`
的豁免只有人能给（R10 §37.3、`review:same_provider` 只对 HUMAN 开），但界面上只能**看**审核记录
（"同厂商审阅 · 谁放通过了"），认领与提交一个按钮都没有 —— 人想审只能去 CLI。

**先不做的三条理由**：

1. **它不是常规路径**。常态是成员窗口互审（cross-provider），管理员窗口也会审；人审是"没有
   合适审阅人 / 需要立刻放行"的兜底。
2. **它要新的状态机语义，不是补个按钮**。人认领审阅会占一条 `review` 记录（有租约、有
   `review_id`、绑定 `attempt_id`），和成员/管理员的审阅**竞争同一条尝试**。先得回答：
   人审和管理员审撞上了谁赢？人认领后超时怎么办？现在没有任何口径，硬上只会造出一种新的
   「界面点了没反应 / 409 看不懂」。
3. **同厂商豁免是安全边**。它允许"自己人审自己人"，而且每个任务只能豁免一次。这条边上界面
   之前，得先把"这次豁免为什么给"的记录面想清楚（现在落在 `task_decisions` 的
   `scope='review_same_provider'`），否则界面上就是一个"点一下就能绕过互审"的按钮。

**要做的话，前置是这三件**（写清，别下次重新想）：

- 定"人审阅与管理员审阅并发"的胜出规则（建议：谁先认领谁持有，另一方 `409` 并显示持有者）；
- 定人审阅的租约与超时回收（照现有 `review_lease` 清扫，不要新造一套）；
- 同厂商豁免上界面时，**必须**同时要求填写理由（后端已有 `reason` 字段），并把它显示在审核
  记录里 —— 否则事后没人说得清是谁放的行。

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
