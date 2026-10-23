# OFFICE-UI-03

### 统一任务卡

任务编号：OFFICE-UI-03
负责 Harness：Mavis
开始时间：2026-10-23 10:20 +08:00
工作区：`.worktrees/mavis/`
依赖任务：OFFICE-UI-02（额度池那张卡已经长在办公室界面上）
真相源引用：R10 §8（预算与额度）、§17（权限矩阵：谁能碰用户自己的 key）、
§39（人机职责：花谁的钱是人的决定）；`backend/app/pool/service.py`（池子不拥有凭据，
它只切换）；`docs/dev-convention.md` H-4/H-7/H-8
当前状态：doing
test_id：MVP-UI-017

| 项 | 内容 |
|---|---|
| 依赖 | OFFICE-UI-02 |
| test_id | `MVP-UI-017` |
| 允许修改 | `backend/app/pool/**`、`backend/app/auth/policy.py`、`backend/tests/test_pool_user_keys.py`、`office/**`、`docs/claims/mavis--OFFICE-UI-03.md`、`docs/tasks/notes/OFFICE-UI-03.md`、`docs/traceability.md`、`TASKS.md` |
| 禁止修改 | `docs/spec/**`、`backend/migrations/**`、`backend/app/tasks/**`、`backend/app/claim/**`、其他 Harness 的工作区与分支 |

## 做什么

用户自己在办公室的**额度池**里加一个 provider 的 API key；免费额度池拿不出额度时，
平台**来问**用户要不要用自己的 key 继续做；用户点了头（用一次 / 以后都用）才花。

三段：

1. **登记**：人加 key（名字、协议、地址、模型、key 本体）→ 平台替他保管
   （`data/user-keys/`，key 永不出接口，只给遮罩）。
2. **问**：免费档与计量档都拿不出额度、而用户又有一把没授权的 key 时，平台
   **记下一个问题**并给 worker 一个能读懂的拒绝（409 `pool_user_key_pending`），
   办公室右栏出现一张「平台来问你了」的卡片。
3. **点头**：用户点「同意用一次 / 以后都用它 / 先不用」→ 授权生效，池子才可以用
   那一档；「用一次」被这一次请求用掉就失效。

## 为什么

### 一、免费额度不是永远够的

额度池现在的三档是 **free → metered → reserve**：reserve（订阅额度）平台**不自己花**，
要花得有人点头。用户的 provider key 正是同一类东西 —— **是他自己的钱**。
平台自己的原则（`backend/app/pool/service.py` 的模块注释）是「池子不拥有凭据，
它只切换」；所以用户这把 key 也只能是第四档 `user`，排在 free / metered 之后，
**平台绝不自己花**。

### 二、平台不接 provider 的 key（这是设计，不是我偷懒）

池子现有的凭据全都**从 CLI 自己的配置里读**（`~/.codex/config.toml`、
`~/.config/opencode/opencode.json`、`~/.mmx/config.json`…），
凭据形式是 `("config", 路径, 字段)` 或 `("file", 路径)`。
管理员 Agent（平台助手）也是**跑管理员窗口自己的 CLI**，用 CLI 自己的登录 ——
它不需要、也没有一个"接收 provider key"的口子。

所以**没有现成接口可以复用**：平台从来没有"收下一个人给的 provider key"这条路。
本任务新开的那条路**复用池子已有的凭据机制**（`credential=("file", 路径)`，
调用时现读），所以池子里仍然只有**一条**凭据通道，不是两套。

### 三、为什么用文件而不是建表

key 是**运行期凭据**，不是业务状态：建表就得进 schema、就得改 `schema_version`、
就得走 R10-fix 复审，而一个明文的 key 躺在一个会被 diff 的表里是坏主意。
落在数据目录下的 `data/user-keys/`（一个 key 一个文件 + 一个索引）与平台令牌
`data/*.key` 同一片地方：都在 `.gitignore` 里、都在本机、不进 schema。

## 怎么做到不出错

1. **不点头就不花**：第四档 upstream 进路由前先过 `_guard_user_upstream()`；
   没点头就抛 409，并**记下问题**给界面。
2. **拒绝要能读懂**：池子的 `PoolError` 以前会变成 500 "internal error" ——
   worker 分不清"没有这个模型"和"用户在等点头"，只会一直重试。现在池子的路由把
   `PoolError` 映射成平台自己的错误信封（`{"error":{"code",...}}`），流式请求
   **先定路由再开流**，所以拒绝是一个能读的状态而不是半路断掉的流。
3. **key 不漏**：接口只给遮罩（`sk-…abcd`）；冒烟断言界面上不出现 key。
4. **只问一次**：同一个 key 只留一个未答的问题（不攒成一堆）。
5. **界面不抢用户正在输入的东西**：办公室每 15 秒重画一次右栏，重画之前把
   **正在填的表单、光标、焦点、各栏滚动位置**存下来，重画之后放回去
   （不然"加一个 key"填到一半会消失，正在看的额度池会跳回顶上）。

## 完成标准

1. `backend/tests/test_pool_user_keys.py` 绿（12 项）：存了不漏、坏输入拒绝、
   不点头不花、once 用一次即失效、always 一直有效、只问一次、路由先问后花、
   未授权直连被拒 409（普通与流式各一）、key 不出现在回答里、
   **「用一次」真的被那一次请求用掉**、**加完 key 立刻可见**、
   **两个数据目录的 key 不互相串**。
2. `office/scripts/verify-office-user-keys.mjs` 绿：额度池里有「自己的 API key」
   区块；列表上有名字和地址、**没有 key 本身**；平台来问时是一张人话卡片、
   三个按钮都在、且这张卡在右栏最上面；点「同意用一次」打的是 approve + mode=once
   且问题消失；正在填的表单在重画后不丢；窄屏（<1180px）也够得着额度池。
3. 真平台实跑：真令牌 + 真数据 + 一个假 provider（本地 4180 端口）——
   未授权时 409 + `pool_user_key_pending`，点头后池子真的调到了那个 provider，
   用量记上了，`once` 用掉即失效，`always` 一直有效。
4. 既有冒烟全绿；`office/` 构建通过；`./scripts.sh check` 三道门禁全绿。
5. `docs/traceability.md` 登记 `MVP-UI-017`。

## 验证结果

| 门禁 | 结果 |
|---|---|
| `backend/tests/test_pool_user_keys.py` | 9/9 PASS |
| `office/scripts/verify-office-user-keys.mjs` | ok（Chromium 实测，拦截 API） |
| 真平台实跑 | 见下方「真跑记录」 |
| `./scripts.sh check` | 见提交记录 |

## 真跑记录（真平台 8849 + 真令牌 + 本地假 provider 4180）

- 加 key（走办公室自己的接口 `POST /api/formal/user-keys`）：列表上出现名字与地址，
  **没有 key 本身**；页面上不出现 `sk-`。
- 平台来问：harness token 调池子要那个模型 → **409**
  `{"error":{"code":"pool_user_key_pending"}}`（改之前是 500 internal error）；
  平台记下问题（`data/user-keys/index.json` 的 `requests`）。
- 界面上出现那张卡片：「平台在问你一件事 · 免费额度用完了，要不要用它自己加的 key 继续做？」
  三个按钮：先不用 / 同意用一次 / 以后都用它。
- 点头「用一次」之后：池子真的调到了那个 provider（假 provider 收到
  `Authorization: Bearer sk-fake-test-key-…`），**200** 且回了它的答案；
  用量记到 `user:k6d9cea`（consumer `harness/codex`，token 5/7）；
  **`once` 用掉即失效**（approval 回到 none，requests=1）。
- 点头「以后都用它」之后：再调一次仍然 200，且 approval **保持** always（requests=2）。

### 走查才发现的六件事

1. **窄屏下额度池整个没了**：`.flat-rail` 在 1180px 以下是 `display:none` ——
   「加自己的 key」这个功能在笔记本上**没有入口**。改成排到下面一行（不隐藏）。
2. **每 15 秒重画会抹掉正在填的表单**：办公室整块重画（右栏 15 秒一轮），
   正在填的 key、光标、滚动位置全丢；「加一个 key」那个折叠还会自己合上。
   现在重画前存（值 + 光标 + 焦点 + 各栏滚动位置），重画后放回去。
3. **「平台来问你了」埋在额度池长卡片的底下**：要展开、要滚到底。
   改成右栏**最上面**一张独立的卡（`flat-card-ask`）。
4. **办公室的所有错误都是静默的**：`state.error` 被写进去，但**从来没有被渲染过** ——
   所以点「同意用一次」返回 404 时，人只看到"点了没反应"。现在顶栏有一条
   能看见的提示（不会被 15 秒刷新抹掉），下一次动手或点「知道了」才清。
5. **网关拼 URL 多了一个斜杠**：`keyId[2]` 是 `"/approve"`（带斜杠），拼成
   `…/k123//approve` → 平台 404。改用 `keyId[3]`。
6. **「用一次」从来没人消费**：`spend_once()` 写了但**没有任何地方调用** ——
   用户点一次「同意用一次」，平台就能一直花。现在记账时消费（记账失败也不留授权）。

### 全量套件里才露出来的第七件

7. **发现缓存的键里没有数据目录**：用户自己的 key 住在 `data/user-keys/`，而缓存键
   （home + relay_ports + 池配置路径）不含它 —— 一个数据目录的 key 会被另一个目录的
   缓存挡住。**单独跑我的用例是绿的，整套跑就红**（缓存是热的）：5 条用例挂掉。
   真机上的表现是 worker 看到"没有这个上游"，而不是"用户在等点头"。
   修法：缓存键加上数据目录 + 加/删 key 后 `invalidate_discovery()`（新 key 立刻算数）。
   证伪记录：把修复 stash 起来 → 5 条红；放回去 → 79 条全绿。

### 还发现但没修的（不在本卡范围）

- 平台的 `GET /tasks/{id}/diff?include_patch=true` 在**正式仓库路径不存在**时回 500
  （`NotADirectoryError`，不是"没有改动"）。这条在 `backend/app/tasks/service.py`，
  是共享文件、另有归属，本卡只让**办公室**不被它带崩（diff 取不到就空着，
  详情照常显示）。

