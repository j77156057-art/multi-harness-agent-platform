# R10-fix-15 草案：平台 Agent 的模型身份（不占 harness 槽位）

状态：**草案 / 待规范复审**。按 `TASK-ALLOCATION-ARBITRATION.md` §117，本文只做"登记输入"，
尚未改动 `docs/spec/0.3-R10.md`、`backend/`、`tools/docstate`。

---

## 0. 一句话

现在的"管理员 harness" `office-admin` 在平台里注册成一个 **harness**（`harnesses` 表一行、
provider=qoder、adapter=qoderclicn-print、`/harnesses` 会列出它）。这让它同时被当成
"可以接活的执行者"和"管理员"，前端只能靠写死的 id 把它从成员表里剔掉。

本草案把它改成**平台 Agent 的模型身份**：

- 它是**本项目自带的平台 Agent**（管理员助理）调用的模型，**不是**管理员，**不是** harness 成员；
- 它**不占** `harnesses` 槽位、不进 `/harnesses`、不能被派单、不能被选成项目管理员；
- 管理员的身份仍然是**群主在开工向导里选的外部 harness 窗口**（`harnesses` 里的一行）。

## 1. 角色（三段，互不替代）

**平台助手 = 平台 Agent**：同一个东西，平台自研的 Agent。下文统一叫「平台 Agent」，
界面文案（`平台助手`）保持不变。它不是 harness、没有 token、不占槽位。

| 角色 | 是谁 | 在哪注册 | 能做什么 |
|---|---|---|---|
| 平台 Agent（界面叫「平台助手」） | 平台自研 Agent：运行时 + 它调用的模型 | 运行时无主体；模型身份进新增的 `platform_agents` 表 | 协调信息传递、定时监控成员进度、判定停滞/超时、**及时**上报管理员；可产诊断/建议。**不做规划、不做分工、不处置任务** |
| 管理员 | 群主在开工向导里选的外部 harness 窗口 | `harnesses` 表（本体） | 拆解、传阅、裁决、分派、领取与提交审核。**可以写代码**（不推荐，不拦截） |
| 成员 | 开工时勾选的 harness 窗口 | `harnesses` 表（本体） | 领取并执行任务；**相关度高的任务可以一起领取**（受 `max_parallel_tasks` 约束） |

### 1.1 群主（用户）的六条口径（2026-10-18 确认）

1. **平台助手与平台 Agent 是同一个东西** —— 平台自研 Agent，不是两个角色。
2. **"监控内容进度"的粒度待定** —— 目前只读任务状态字段（status/owner/attempt/lease）；
   是否要读成员交上来的正文、读到什么程度，群主还要再想。**先不改**。
3. **管理员可以写代码** —— 不推荐（审核多个 harness 的提交本身要时间，自己写会拖慢进度），
   但平台**不拦截**。不得以"管理员不该写代码"为由拒绝或报错。
4. **上报的时效性是硬要求** —— 信息会过期，报晚了等于没报。平台 Agent 的存在意义之一
   就是把"该上报"这件事**尽快**变成一条消息，而不是等人来点。
5. **历史消息作者（O1）** —— 保留原作者不变，另记 `sender_type='platform_agent'` 只读展示。
   不重写历史，不把作者改成别的 harness。
6. **成员不必一次只接一条** —— 相关度高的任务可以一起领取；约束是
   `harness_registrations.max_parallel_tasks`（当前所有窗口都是 1），不是"一次一条"的硬规则。

## 2. 冲突（现状为什么不行）

| 出处 | 规定 / 现状 |
|---|---|
| `backend/app/admin/__init__.py` | 平台只有 `human`/`harness` 两种主体，管理员是受 `AP_ADMIN_SUBJECTS` 约束的 harness |
| `backend/app/auth/policy.py` `ADMIN_HARNESS_KEYS` | 管理员能力挂在 harness 主体上，因此这个主体必须能通过 harness 认证 |
| `/harnesses` | 把 `office-admin` 当普通 harness 列出 → 前端必须写死 id 剔除 |
| office-preview `main.js` / `project-workspace.js` | 两处都靠字符串 `'office-admin'` 判身份（重复、易漂移） |
| §17 / §38.11 | 没有"平台 Agent"这一角色，也没有"模型身份"的注册表 |

## 3. 裁定

### 3.1 新增实体 `platform_agents`（§8 canonical）

| 列 | 含义 |
|---|---|
| `agent_id` | 平台内的稳定标识（本部署：`platform-assistant`，**不含 provider 名**） |
| `group_id` | 归属项目（群）。每个项目一份 |
| `provider` / `model` | 模型身份，例：`qoder` / `qoder-cli-cn`、`deepseek` / `deepseek-v4.1-flash` |
| `channel` | 调用通道（`cli` / `http`），与 harness adapter 分开登记，不复用 `adapter_version` |
| `status` | `online` / `offline` |
| `registered_by` | 登记人（人类主体） |
| `registration_version` | 与 harness 同口径的版本号，便于回放 |

不变量：

- `platform_agents` **没有** `harness_id` 列，也**不出现**在任何 `assigned_harness_ids`、
  `owner`、`reviewer_harness_id`、`allocation` 里。四张表各加断言（实现层的测试，不是注释）。
- 同一 `group_id` 最多一行（部分唯一索引 `WHERE status='online'`）。

### 3.2 `harnesses` 里不再有 `office-admin`

**已落地（2026-10-18）**：网关注册表 `BASE_ADAPTERS` 里的 `office-admin` 三行已删除。
它不是一个窗口身份（`<base>-member|window-<slot>`），删掉之后 `/status` 的 `harnesses[]`
不再包含它，前端的管理员候选与成员表自然就干净了 —— 不再需要任何写死 id 的剔除。

**数据库里那一行仍在**（`office-formal` 群）。删除它属于数据迁移，必须等下面三条都成立：

1. 没有任务的 `assigned_harness_ids` / `owner` / `reviewer_harness_id` 指向它（office-formal 群里要为 0）；
2. 没有在途的 review 租约以它为 reviewer；
3. `AP_ADMIN_SUBJECTS` 改为指向**群主选中的那个 harness**，而不是固定 `office-admin`。

> **O1 已裁定（2026-10-18，见 §1.1 第 5 条）**：保留原作者不变，另记
> `sender_type='platform_agent'` 只读展示。不重写历史、不把作者改写成别的 harness。
> 因此第 4 步（删 `harnesses` 里那一行）**可以执行**，不再被 O1 阻塞；
> 唯一的门是"该群 0 处引用 + 无在途 review 租约 + `AP_ADMIN_SUBJECTS` 已切走"。

### 3.2a 模型通道跟着管理员身份走（已落地）

细化曾经写死 `qoder_command`。现在 `AdminService._model_channel()` 用与 worker 同一张
适配器表（`ADAPTER_TYPES` + `base_harness_id`）解析管理员的身份：

- 管理员是 `qoder-window-1` → 走 qoder CLI；是 `codex-window-1` → 走 codex CLI；
- 身份解析不出 base harness → **fail closed**，不退回任何默认通道；
- `AP_ADMIN_MODEL_CHANNEL`（默认 `auto`）可以显式把通道钉到另一条 CLI，用于
  "管理员窗口的 CLI 不在本机"的部署；未知值拒绝启动。

输出解析同样走适配器自己的 `parse_output`（原先在本模块里复制了一份 qoder 的
JSONL 解析），只保留首行的 CLI（CodeBuddy）收到的是 stdin 上的完整提示词。

这直接推出一条新约束：**管理员必须是一个窗口身份**。`office-admin` 这类模型身份
解析不出 CLI，因此不能当管理员 harness —— 这正是 §0 的结论。

### 3.2b 计划的两个方向（已落地）

`plan:reject` 加入 `ADMIN_HARNESS_KEYS`。理由：§17 第 426 行把 `plan.reject` 给的是
"人类驳回并记录原因"，与 `plan.approve` 同属"人类管理员/负责人"那一列；管理员 harness
是那个人在部署里的替身，只放行 approve 等于让它只能批不能退。不新增权限键，
并新增一条机器断言（`test_admin_harness_key_set_is_exactly_the_delegated_rows`）钉住
"加性表里的每一行都是 §17 已登记的键"。

**配套修掉一处审计假话**：`approve_plan` / `reject_plan` 原来把事件里的
`actor_type` 写死成 `"human"`，而 `actor_id` 用调用者声明的 id。管理员一旦能 approve
（早就如此）或 reject（本轮新增），审计里就会出现"人类批准了这张计划"，而实际是
harness 批准的。现在两个方法都接受 `decided_by_type`，由路由用
`_actor_type(principal)` 传真实主体类型；auth off 时才回落到 `human`（那时调用者就是
人类操作者）。这不改任何事件名或 schema —— `actor_type` 一直是文本列。

### 3.3 权限：模型身份不是主体

- `platform_agents` **不签发 token**，因此不进 §17 权限矩阵、不进 `AP_ADMIN_SUBJECTS`。
- 平台 Agent 的输出由**平台助手**（运行时）消费；助手对平台只有两条写入：
  `POST /groups/{group}/messages`（上报）与巡检记录（本地追加）。与现状一致。
- `AP_ADMIN_SUBJECTS` 的语义**不变**（加性、fail-closed、默认空），只是默认值不再是
  `office-admin`，而是群主配置的外部 harness。

### 3.5 管理员可以写代码（不推荐，不拦截）

群主 2026-10-18 口径。实现上**不需要改动**：`task:claim` 在 POLICY 里本来就是
`{HARNESS}`，管理员 Harness 也是 harness 主体，因此它一直能领取任务。这里要钉的是
**不许加反向拦截**：

- `admin/__init__.py` 的职责注释不再写"管理员只细化+分派+审核"，改为"主要做这个，
  但也可以接活"。
- 细化提示词里已有的"执行者必须是注册成员"不变；管理员本身若在成员表里，也可以被派。
- 任何"管理员不能 claim / 不能 submit_result"的守卫都算回归。

理由（记录，不是规则）：审核多个成员交上来的成果本身要时间，管理员再自己写会拖慢进度。
所以是**不推荐**，不是**禁止**。

### 3.6 成员可以一次领取多条相关任务

群主 2026-10-18 口径。现状：

| 层 | 现状 |
|---|---|
| `ClaimRequest.task_ids` | 已经是**列表**，一次 claim 可以带多条（`partial_allowed` 控制部分成功） |
| `harness_registrations.max_parallel_tasks` | 这才是并发上限；当前 10 个窗口全是 `1` |
| 网关 | `max_parallel_tasks: 1` 写死两处（复制窗口 / 新建 base 窗口） |
| worker | `--once` 一次跑一条，是**用法**不是平台限制 |

所以"一次只能接一条"从来不是硬规则，是三处默认值叠加的结果。本草案只登记语义，
不改实现：

- 相关度高的任务**可以**一起领取；"相关"由管理员在派单时表达（同一 `exclusive_paths`
  邻域、同一条验收链），平台不替管理员判断相关度。
- 放开的上限走 `max_parallel_tasks`，属于**注册面配置**，不是代码常量。
- 不做"自动把相似任务分给同一个人"——那是分工，属于管理员。

### 3.7 上报的时效性

群主 2026-10-18 口径：信息会过期，报晚了等于没报。因此：

- 上报由**事件触发**（停滞、超时、无人可派、重复派发、待审查、已放弃、租约失效），
  不是等人来点按钮；
- 每条上报带**发生时间**与**当时的任务序号**，读者能判断它是否已过期；
- 平台 Agent 可以合并同一窗口内的多条线索，但**不得延迟到"凑够一批"才发**；
- 巡检间隔是上限，不是节流理由。

未定（群主还要再想，见 §1.1 第 2 条）：平台 Agent 读不读成员交上来的**正文**，
读到什么程度。在定之前只读状态字段。

### 3.4 `/status` 暴露模型身份

`GET /api/formal/status` 增加：

~~~json
{ "admin": { "configured": true, "identity": null },
  "platform_agent": { "agent_id": "platform-assistant", "provider": "qoder", "model": "qoder-cli-cn", "status": "online" },
  "harnesses": [ /* 不含平台 Agent */ ] }
~~~

`admin.identity` 从"写死的 `office-admin`"改为"没有身份，由群主在向导里选"
（`identity: null` + `source: "group-selection"`）。前端据此**不再写死 id**：
只要 `harnesses[]` 里没有它，它就自动不出现在成员表与管理员候选里。

## 4. 前端（office-preview）已按此语义实现

已落地、不依赖本次后端改动的部分：

- `main.js`：`ADMIN_IDENTITY = formalStatus?.admin?.identity`，`isPlatformAgent()` 用它判定，
  不再写常量字符串；成员表与管理员候选都从过滤后的 roster 生成；
  `openOpeningGuide()` 每次打开向导重算候选（新登记窗口立刻可选，平台 Agent 永不出现）。
- `project-workspace.js`：成员名单的剔除同样应改为读取 `admin.identity`（当前仍是常量，
  等后端 §3.4 落地后一并替换）。

## 5. 验收

- `PA-001`：`/harnesses` 不含平台 Agent；`assigned_harness_ids` 出现它 → 派单被拒（`invalid_harness`）。
  **已达成**：网关不再注册 `office-admin`，`/status` 的 `harnesses[]` 里没有它。
- `PA-002`：`platform_agents` 同一群最多一行；换模型只改 `provider/model`，`agent_id` 不变。
- `PA-003`：平台 Agent 不出现在 §17 的任何一行；拿不到 token。
- `PA-004`：管理员候选 = `harnesses` 中 `status=online AND capabilities∋coding`，**不含** platform agent；
  向导**不预选**（`verify-lobby-entry.mjs` 已断言）。
- `PA-005`：迁移删除 `office-admin` 之后，office-formal 的历史任务 0 处引用它（SQL 断言）。
- `PA-006`（新增）：细化走**管理员自己的**通道。`test_refine_uses_the_channels_of_the_administrator_identity`、
  `test_refine_refuses_an_identity_with_no_registered_channel`、
  `test_refine_channel_can_be_pinned_by_configuration`、
  `test_refine_sends_a_long_prompt_on_stdin_for_stdin_only_clis` 四条钉住。
- `PA-007`（新增）：`plan.approve` / `plan.reject` 的审计事件里 `actor_type` 等于
  调用者的真实主体类型，不是写死的 `human`。

## 6. 实施顺序（每步单独提交，每步都能独立回滚）

1. ~~**代码先就位（加性）**~~ **已完成**：网关注册表删掉 `office-admin`；前端已只读
   `admin.identity` 判定；后端的细化通道改为按管理员身份解析（§3.2a），并放行
   `plan:reject`（§3.2b）。这一步不动数据，`harnesses` 表里的历史行仍在。
2. **数据迁移**：`platform_agents` 建表 + 每群一行 + 把 `office-admin` 的模型身份写进去；
   **不删** `harnesses` 那一行。
3. **切换管理员** —— **已完成（2026-10-18）**：注册 `qoder-window-admin`（真实 UI 注册）、
   签发凭据、`AP_ADMIN_SUBJECTS` 与 `OFFICE_FORMAL_ADMIN_TOKEN_FILE` 同步切换；
   细化 / 审核鉴权 / 管理员面板均已实测。见
   `handoff/PLATFORM-AGENT-MODEL-IDENTITY-2026-10-18.md` §8。
4. **删除** `harnesses` 里的 `office-admin` 行（唯一不可逆的一步；O1 已裁定，见 §3.2）。
5. **收紧**：`/harnesses` 过滤掉 `platform_agents` 里的身份；补 `PA-001`～`PA-006` 测试；
   docstate 断言数与 README / `dev-convention.md` 同步（`discipline [counts]` 会红）。

第 1、3 步已完成。第 4 步不再被 O1 阻塞；剩下的门是"该群 0 处引用 + 无在途
review 租约"，以及它确实不可逆。注意切换后仍有两条尾巴：`office-admin` 那一行还在
`harnesses` 里（派单下拉仍能看到），`office-admin.key` 未吊销（已无权却仍可作普通 harness）。

另一个切换时暴露的结构问题：**管理员是全局一份（`AP_ADMIN_SUBJECTS`），而开工向导
让群主按项目选一份**。两者不一致时，后端只认全局那个。见 §7 的 O6。

## 7. 开放项（需群主裁定）

- **O1 —— 已裁定（2026-10-18）**：保留原作者，另记 `sender_type='platform_agent'`
  只读展示；不重写历史。见 §1.1 第 5 条与 §3.2。
- **O2**：平台 Agent 的模型由谁配置——网关配置（本机 JSON）、`platform_agents` 表、还是环境变量？
- **O3**：每项目一份（`group_id` 非空）还是全平台一份（`group_id` 可空）？本草案取"每项目一份"。
- **O4（新增，待群主再想）**：平台 Agent 监控到**内容**的哪一层？当前只读状态字段
  （status/owner/attempt/lease）。要不要读成员交上来的正文、读到什么程度、
  读了之后能不能影响上报判定——见 §1.1 第 2 条。
- **O5（新增）**：`max_parallel_tasks` 要不要在开工向导/注册面暴露给群主配置？
  语义已登记（§3.6），但"谁能改、改到几"还没定。
- **O6（新增，切换时暴露）**：管理员到底是**全局一份**（`AP_ADMIN_SUBJECTS`，当前实现）
  还是**每项目一份**（开工向导里选的那个）？现在两个都在：向导让群主按项目选，
  后端却只认全局白名单，回执里记的那一份不产生权限。要么把白名单做成按群
  （`group_id -> admin subject`），要么承认管理员是平台级、把向导那一步改成"确认"
  而不是"选择"。

## 8. 切换记录（2026-10-18）

管理员已从 `office-admin` 切到真实窗口 `qoder-window-admin`，实测通过；同时修掉
网关硬编码管理员 id、`/status` 报常量、管理员面板没有挂载点、验证脚本读不存在的
`debug()` 四处。细节见 `handoff/PLATFORM-AGENT-MODEL-IDENTITY-2026-10-18.md` §8。
