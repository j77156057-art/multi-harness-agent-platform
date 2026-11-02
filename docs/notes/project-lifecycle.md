# 项目是什么：一个 id、一个标签、一个回收站

状态：2026-11-02 实现（建/改名/回收站/到期清扫）。作者：助理 agent。

## 0. 起因

界面里的「项目」一直是浏览器本地对象（`localStorage`），而平台上也有一张 `projects` 表 ——
两边各叫各的。于是出现两个说不清的事：

- 界面上建的第二个项目，平台上一行都没有；换个浏览器打开，它又没了；
- 「移除」只是在浏览器里抹掉一条记录，平台的库一点没动。

**现在没有云端**，所以这个区分本身就不成立：界面里的项目**就是**平台上那一个。这次把它接上。

## 1. 三条口径（已实现）

1. **id 是随机生成的，名字只是给人看的标签。**
   新建项目时 id 由**服务端**生成（`proj-` + 12 位十六进制），界面不填、也不许决定。
   所以：同名可以有多个项目；名字改了不影响任何东西。
2. **改名只改名字。** 任务、群、事件、已合入的提交都挂在 id 上，改名不动 id。
   改名复用 `project:create` 这条权限（同一档：改一条项目的标签），不为它新开权限边。
3. **移除 = 进回收站，一周内可找回。** `projects.trashed_at` 记下时间；默认列表不带它，
   `include_trashed=1` 带出来（界面据此显示"还剩几天"）。再次点移除**不会**重置时间
   —— 那等于无限续期。

平台侧的读写面：

| 动作 | 接口 |
|---|---|
| 建（连带一个主群） | `POST /projects`、`POST /projects/{id}/groups` |
| 改名 | `POST /projects/{id}/rename` |
| 移除 | `POST /projects/{id}/trash` |
| 找回 | `POST /projects/{id}/restore` |
| 列表（含回收站） | `GET /projects?include_trashed=1` |

网关把上面这几条包成 `/api/formal/projects…`，界面只认网关。

## 2. 切项目 = 换群（已实现）

后端一直支持多个项目/群，但网关原先把所有读接口钉在启动时那一个 `config.group` 上 ——
于是"新建的项目真在平台上、却打不开去干活"。现在按**请求**取群：

- 前端每个 `/api/formal` 请求带 `X-Office-Group: <本项目的群 id>`（`office/src/office-scope.js`），
  **没有就不带** —— 老项目（本地对象、没有 `platformGroupId`）照旧用启动时那个群，默认行为不变；
- 网关用这个头解析出本次请求的群；**不带头就是启动时那个**，并**不做**额外校验（启动时已验过）；
- 带了头就要核：群存在，且它所属的项目**不在回收站**。核不过 `403 group_not_available`，
  结果缓 15 秒（一次刷新会发好几个请求，不必每个都问两遍平台）。

为什么敢让前端指定群：这是本机**单用户**的观察面，而且真正的关口在平台那一侧 ——
令牌是操作员的、数据本来就按令牌可见。前端只报 id，**能不能用由平台事实决定**，不让浏览器判权限。

真跑（同一个 office 实例，只换请求头）：

| 请求带的群 | 结果 |
|---|---|
| 不带头 | `200`、4 条任务，全属启动时那个群 |
| 另一个项目的群 | `200`、**0 条任务** —— 真的换过去了 |
| 回收站里项目的群 | `403 group_not_available` |
| 瞎编的群 | `403 group_not_available` |

**起 worker 这一条也跟上了**（`POST /harnesses/provision`）：worker 落在**请求所在的群**，
不是启动时那个。判定分三种，都按真进程的命令行来：

| 情况 | 结果 |
|---|---|
| 没有 worker 在跑 | 起一个，`--group` 用这次请求的群 |
| 有 worker，**同一个群** | 什么都不做（不重复起第二个） |
| 有 worker，**另一个群** | `409`，并写明"它已经在为 X 干活；一个进程一次只服务一个群，要换先把它停掉" |

为什么要读回 `--group`：一个 worker 一次只服务一个群。不读的话，人切到另一个项目再点
「让这个窗口能干活」，就会给同一个窗口起第二个进程 —— 两个进程用同一个凭据各领各群的活，
出问题时没人说得清是谁领的。

真跑（`qoder-member-1`）：默认群起 → 命令行 `--group flow-1101-g1`；带另一个群再点 →
`409` 且没有第二个进程；停掉后用那个群再点 → 命令行换成 `--group proj-…-g1`；最后停掉收尾。

## 3. 回收站到期之后：删到哪一层（已定，已实现）

「一周之后彻底删除」这句话有三种落法，代价差很多：

| 方案 | 删什么 | 代价 |
|---|---|---|
| A 连带任务删，事件流留底 | 项目、群、任务、尝试、工作树记录 | 界面与列表清空；`task_events` 仍可追溯到"发生过什么" |
| B 连事件一起删 | 上面全部 + 事件流 | 真·清空，但过去发生过的事不可追 |
| C 只删项目与群 | 两行记录 | 名下任务会变成没有归属的孤儿 |

**选了 A**（2026-11-02 由人拍板）。理由：`task_events` 是 append-only 的"发生过什么"，
删掉之后"这条活为什么被卡住、谁审过、合入过什么提交"就再也查不到了；而"这条活现在是
什么状态"是**投影**，投影跟着项目一起走没什么可惜的。界面清空，但过去发生过的事还能追。

### 3.1 删什么、不删什么

实现：`backend/app/projects/purge.py`（`TRASH_RETAIN_DAYS = 7`、`purge_project`、
`purge_due_projects`），挂在清扫家族的最后一位（`sweeps.py` 的 `sweep_project_trash`、
`runner.py` 的 `SWEEPS`）。排在最后是因为它删的是"已经没人看的东西"，不该插在状态推进中间。

| 层 | 删 |
|---|---|
| 项目自身 | `projects`、`groups`、`integration_health` |
| 活与执行 | `tasks`、`task_attempts`、`task_claims`、`task_outputs`、`task_decisions`、`task_dependencies`、`sweep_progress`、`resource_leases`、`leases`、`workspaces`、`artifacts` |
| 审核与验证 | `reviews`、`verify_jobs`、`verification_receipts` |
| 分配与传阅 | `allocations`、`allocation_decisions`、`allocation_rounds`、`circulation_rounds`、`circulation_reviews` |
| 申请 | `applications`、`application_materials`、`application_tasks` |
| 消息 | `task_messages`、`message_deliveries` |
| 预算与旁路 | `budget_reservations`、`budget_incidents`、`side_effect_intents`、`alert_deliveries` |

**不删**：

- `task_events` —— 口径本身；
- 归不到某个项目上的记账（`command_idempotency`、`jsonl_exports`）—— **只删得掉自己认得的
  东西**：宁可留几行孤儿，也不能顺手清掉别的项目还在用的记账。

删之前先按**真实 schema** 过一遍列名（`PRAGMA table_info`），不是写死"任务表有 task_id 和
attempt_id"：`sweep_progress` 只有 `task_id`、`resource_leases` 只有 `attempt_id`，写死两句
`IN (...)` 会撞上"没有这一列"而让整条清扫报错 —— 那等于回收站永远清不掉。

**删除顺序也被外键钉住了**（连接是 `PRAGMA foreign_keys=ON` 的）：子行先删合法，父行先删会被
SQLite 直接拒掉、整个事务回滚。所以表顺序照 `PRAGMA foreign_key_list` 排，不是照"看起来顺眼"排。
最容易排错的是分配那几行：`applications → allocation_rounds`、`allocations →
allocation_decisions → applications` —— 写成"先删轮次"在**没有申请数据**的库上完全看不出问题，
在真有申请的库上一删就炸。这条有测试钉着（见 §5 的 `foreign_key_check` 那条）。

## 4. 界面上的样子

- 项目卡：`进入 / 改名 / 移除`（改名只在有 `platformProjectId` 的卡上出现）；
- 「移除」的确认框按项目类型分岔：转接项目问"去掉哪一层"（本地 / 连仓库说明），平台项目
  问"移到回收站？"并写明"一周内可找回"；
- 大厅底部有**回收站**区：每个项目一行，写"还剩 N 天可找回"，右边一个「找回」。
  放这块的理由很直接：只把话说在"移除"那一刻，人之后就没有找回来的入口了。

## 5. 验收

- 后端 `tests/test_project_trash.py` 7 条：随机 id、同名不冲突、改名不动群、空名被拒、
  移除后默认列表没有但 `include_trashed` 还在、重复移除不重置时间、找回后回到列表、
  不认识的项目给 404（不是静默成功）。
- 后端 `tests/test_project_purge.py` 8 条：到期项目连带任务删掉且**事件流还在**；没到期的
  一个字不碰；只删到期那个、别家照旧（任务与事件都在）；从没进过回收站的项目哪怕是去年的
  也不删；清扫报告写清删了什么；清扫在 `SWEEPS` 的最后一位；带申请与裁决的项目能删掉且
  `PRAGMA foreign_key_check` 为空（这条钉的是删除顺序 —— 把 `applications` 挪到
  `allocation_rounds` 后面，它就红）；整套清扫跑完不会把删掉的东西"复活"也不会崩。
- 迁移 `020_project_trash.sql` 只在真实库副本上验过：加列 + 加索引，9 个既有项目
  `trashed_at` 全为 NULL（不在回收站）。
- office 校验：`npm run verify` 30/30 通过、24 跳过、0 失败。
