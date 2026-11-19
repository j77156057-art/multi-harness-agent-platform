# 平台体检工具 · 使用说明与口径文档

一句话定位：`tools/doctor/doctor.py` 是一个**只读、零额度**的体检工具，给运营者看——不派单、不调模型、不跑发现逻辑、不新增第三方依赖，只把窗口、额度、账本三面的现状读出来，并按固定口径给出结论。

本文与命令实现逐字对齐。所有字段名、路径、阈值、状态字面量都以 `tools/doctor/doctor.py` 里的常量表为准；`JSON_TOP_FIELDS`、`JSON_FACE_FIELDS`、`JSON_ITEM_FIELDS`、`JSON_SCHEMA`（`SCHEMA_VERSION` 为 1）是 `--json` schema 的唯一权威定义，本文不另立一套。

---

## 1. 怎么用

### 1.1 两条命令

```text
python tools/doctor/doctor.py            # 人读报告
python tools/doctor/doctor.py --json     # 一个 JSON 文档
```

两条命令都只读本机平台数据，退出码语义相同。

### 1.2 stdout 约定

- 人读模式：报告打到 stdout；参数错误（未知参数）的用法说明与错误打到 stderr。
- `--json` 模式：**stdout 始终只有一个 JSON 文档**，不混任何日志、进度或提示。即使是读失败、体检自身意外、参数错误，也在同一个 JSON 文档内表达——读失败由 `error` 字段承载，消息以「读不到」开头（例如「窗口面：读不到 state.db（…）」）；参数错误也写进 `error`，消息是「未知参数：…」。因此 stdout 可以无条件交给任何 JSON 解析器。
- 输出统一按 UTF-8 写出；宿主不支持重新设置编码时保持原样，不会因编码问题起不来。

### 1.3 退出码：只有 0、1、2 三种

| 退出码 | 含义 | 判据（与实现同一处） |
|---|---|---|
| 0 | 体检通过（零发现） | `error` 为空且 `findings` 为空 |
| 1 | 发现问题（至少一项发现） | `error` 为空、`findings` 非空 |
| 2 | 读不到平台数据 | `error` 非空，即体检自身跑不起来 |

补充口径：

- **不存在第四种退出码。** 不要用「3」「-1」之类表示别的状态；实现只返回 0/1/2。
- **退出码 1 的统一判据**：只看 `findings` 是否非空，不按发现条数分级、不按面分别编码。任一面的任一发现都会让整体退 1。
- **2 优先于 1**：只要发生读失败，即使别的面同时有真实发现，也一律退 2。发现不会因此被抹掉——`findings` 里仍然保留，只是退出码取 2。
- **单面读失败时其余面照常产出**：失败的那一面给出「读不到 X」并以 `checked=false` 的空结构落盘，其余可读的面照常给出结论；整体仍退 2。
- **`ok=true` 仅当退出码为 0**：`ok` 的值与「是否退 0」严格一致；读失败或有发现时都是 `ok=false`。

### 1.4 参数错误

未知参数（除 `--json` 外的任何参数）：

- 人读模式：把用法「python tools/doctor/doctor.py [--json]」和「未知参数：…」打到 stderr，退出 2；
- `--json` 模式：stdout 仍只有一个 JSON 文档，`error` 里写「未知参数：…」，退出 2。

### 1.5 人读报告的写法约定

人读报告按「**动作 + 对象**」逐行写，例如「检查窗口 X：在场，可接活」「检查模型 Y：档位 …，属主窗口 …」「结论：…」。它只摆**显示名**，不摆哈希、不摆内部编号；只有在窗口没有显示名时，才退回到它唯一的标识——那是分辨窗口所必需的，不算「摆编号」。

每面末尾固定有一行「本面检查了什么 / 没检查什么」，含义是**证据边界**：前半句说明该面的结论建立在哪些数据上，后半句说明哪些事项不在这面的能力范围内。不要把「本面没发现」读成「世界上不存在」。

---

## 2. 数据根与只读快照（三面共用）

### 2.1 唯一数据根

平台数据一律以 `app.config.settings.data_dir` 为**唯一根**：

| 数据 | 路径 |
|---|---|
| 数据库 | `data_dir/state.db` |
| 池子声明 | `data_dir/pool/config.json` |
| 用户 key 清单 | `data_dir/user-keys/index.json` |

`settings.data_dir` 可用环境变量 `AP_DATA_DIR` 覆盖；未设置时默认是仓库根的 `data/`。体检不认相对当前工作目录的字面路径，也不走 `pool_config_path()` 的 cwd 回退——只认 `settings.data_dir` 下的那一份。数据库路径由 `data_dir/state.db` 直接拼出（不另读 `settings.db_path`）。

### 2.2 快照：复制 `-wal` / `-shm`，用 `mode=ro`，不用 `immutable=1`

读库前先把 `state.db` **连同 `-wal` / `-shm`** 复制到一个临时快照目录，再以 `mode=ro` 打开快照，并设置 `PRAGMA query_only = ON`。

- 为什么连边车一起复制：`immutable=1` 会忽略 `-wal` 里尚未 checkpoint 的**已提交事务**，读出来是过期数据。复制 `-wal` / `-shm` 后以 `mode=ro` 打开，SQLite 会正常回放 WAL，读到最新已提交状态。
- **零写入、零边车只针对真实 `data_dir`**：所有复制与打开动作都落在临时快照目录，工具退出时整个目录被清掉；真实 `data_dir` 只读不写，也不留下 `-wal` / `-shm` 边车。**快照目录里的边车不计入**「零边车」，那是快照自己的文件，不属于真实平台数据。
- 快照目录建不起来、打不开、复制失败，都按「读不到 state.db」上报（退 2），绝不退化成空库。

### 2.3 读失败不等于没有数据

这是全篇最重要的一条口径。读不到任何一块数据（库、表、池子声明、用户 key 清单）时，该块以**读失败**上报，绝不当成「空数据」「没有」「零」：

- 读不到表 → 该面 `checked=false`，`error` 非空，整体退 2；
- 读不到池子声明文件（文件存在但坏 JSON、结构非法、读不了）→ 额度面读失败，退 2；
- 读不到用户 key 清单（存在但坏 JSON、不是对象、读不了）→ 额度面读失败，退 2。

唯一的「空是诚实的」例外写在各节里：池子声明文件**不存在** = 没有额外声明（采用默认槽位）；用户 key 清单文件**不存在** = 还没有给平台任何 key。这两者都不是读失败。

---

## 3. A 窗口面：现在有几个窗口真的能接活？

### 3.1 数据来源

| 表 | 用途 |
|---|---|
| `harnesses` | 窗口主表：`status`、`registration_version`、`model_mode`、`model_id`、`removed_at` |
| `harness_registrations` | 取当前登记版本对应的显示名 `name` |
| `harness_presence` | 在场快照：`harness_id`、`seen_at` |
| `auth_tokens` | 统计未吊销（`revoked_at IS NULL`）的 harness 凭据数 |

窗口面只统计 `harnesses.removed_at IS NULL` 的行；**软删窗口一律排除**（软删的含义是「不再用它」，但不抹掉它存在过的证据）。

### 3.2 在场（live）的唯一判据

在场只看 `harness_presence` 快照，不看 `harnesses.status`。判据与实现一致：

- 读取每行的 `seen_at`，解析失败即不在场；
- `age_seconds = 当前 UTC 时间 - seen_at`；
- `live = age_seconds <= PRESENCE_FRESH_SECONDS`。

阈值**固定 90 秒**，直接复用平台常量 `app.adapter.presence.PRESENCE_FRESH_SECONDS`（值为 90.0），不另立第二套判据。心跳是 20 秒一次，90 秒约等于允许连丢 4 次。

`live` 就是 JSON 里每个窗口项的 `live` 字段，与人读报告的「在场 / 进程不在场」同源同值。

注意：体检不使用 `presence_snapshot()`，因为那个函数会把「读不到表」吞成空表；体检要求读不到就报错（见 2.3）。

### 3.3 僵尸窗口

僵尸 = **未软删、已注册的窗口**，且 `harness_presence` 快照里**没有 `live=true` 的记录**。

`harness_presence` 表只 upsert（同一 `harness_id` 冲突时更新 `role` 与 `seen_at`），**从不删除行**；因此「表里有这一行」不等于「它还活着」，只有 `live=true` 才算在场。心跳过期、`seen_at` 解析不了、整行缺失，都归为僵尸。

### 3.4 usable 的充要条件

`usable = true` **当且仅当** `reasons` 为空；`reasons` 里每出现一条，就说明该窗口不能接活。判定顺序固定：

| 代码（`reasons` 里的值） | 中文含义 | 触发条件 |
|---|---|---|
| status_not_online | 登记状态不是 online | `harnesses.status` 不等于字面量 `online` |
| registration_version_missing | 没有当前登记版本 | `registration_version` 为空 |
| no_credential | 没有可用的平台凭据 | 未吊销的 harness 凭据数为 0 |
| not_live | 进程不在场 | `live` 为假（见 3.2） |
| model_missing | 池子模式但没有指定模型 | `model_mode` 为 `pool` 且 `model_id` 为空 |
| model_not_in_pool | 指定的模型不在额度池里 | 池子模式、`model_id` 非空，但在合并模型清单里查不到 |
| model_tier_undetermined | 模型档位未定 | 池子模式，模型查到了，但档位是「未定档」 |

「具备可用模型」的口径与 B 面一致：

- `model_mode='cli'`（默认值，列值为空也按 `cli` 处理）时，窗口用的是自己 CLI 配置里的模型，**`model_id` 为空不据此判不可用**，也不查池子；
- 只有 `model_mode='pool'` 时才查档位；
- **档位未定不算可用**：命中 `model_tier_undetermined` 的窗口 `usable=false`。

`live` 与 `usable` 是两个维度：在场是 usable 的必要条件之一（不在场必有 `not_live`），但在场也可能因状态、凭据或模型档位而不可用。

### 3.5 结论与发现

- 结论问题：**现在有几个窗口真的能接活？** 对应 `usable_count`（`usable=true` 的窗口数）与 `windows` 列表。
- `zombies` 是僵尸窗口的 `harness_id` 列表。
- 发现：
  - 每个僵尸窗口产生一条 `zombie_window`（「窗口 X 登记在册但进程不在场」）；
  - 若登记在册的窗口**不为零**且 `usable_count=0`，额外产生一条 `no_usable_window`（「登记在册的 N 个窗口没有一个能接活」）。登记表里一个窗口都没有时，不产生这条发现。

### 3.6 本面检查了什么 / 没检查什么

检查了登记状态、进程在场（心跳阈值 90 秒，复用平台常量）、凭据、接活条件与模型档位；**没检查** CLI 是否安装在本机，也没有实际派单。

---

## 4. B 额度面：现在有没有任何一条通道会花按量的钱？

结论问题：**按当前声明的档位，现在有没有任何一条通道会花按量的钱？** 答案由 `models` 的档位与 `paid_channels` 给出，不看实际账单。

### 4.1 数据来源（两份文件分开读、分开报错）

| 来源 | 用途 |
|---|---|
| `data_dir/pool/config.json` 的 `cli_slots` | 运营者声明的 CLI 槽位（档位、模型） |
| `data_dir/user-keys/index.json` | 用户自己 key 清单里声明的模型 |
| `harnesses`（`model_id`） | 窗口在用的模型与属主反查 |
| `app.pool.sources.DEFAULT_CLI_SLOTS` | 内置默认槽位，与声明合并 |

池子声明与用户 key 清单**分开读取、分开记错**：任一份读失败，额度面都判读失败（`checked=false`），整体退 2。窗口面只有在「存在池子模式窗口且指定了模型」时才依赖这份合并口径；否则不会因为额度面依赖起不来而跟着失败。

### 4.2 模型清单与档位

- 模型清单 = **合并后的 `cli_slots`（含 `DEFAULT_CLI_SLOTS`）与 `harnesses` 在用 `model_id` 的并集**。
- 合并口径复用 `app.pool.sources._merge_slots`：同 `(name, model)` 的声明**覆盖**内置项；`enabled: false` 的声明，给了模型就移除对应的那一项，只给名字不给模型时移除该 CLI 的全部槽位（等于整个 CLI 停用）。
- CLI 槽位的模型 id 写作「槽位名/裸模型名」，档位取槽位声明的 `tier`，按 4.3 的映射表上报。
- 用户 key 清单里的模型追加进清单（已在清单里的同名 id 不覆盖原档位），新追加的档位为 user。
- 属主由 `harnesses.model_id` **反查**：未软删、`model_id` 非空的窗口，按字面值精确归到对应模型 id；没有窗口在用的模型，`owners` 为空。
- 窗口在用、但既不在合并槽位里、也不在用户 key 清单里的 `model_id`，追加进清单；其档位先按槽位的裸模型名别名查，查不到就是「未定档」。
- `paid_channels` 是档位为 paid 的模型 id 列表；每一条产生一个 `paid_channel` 发现（「模型 X 的档位是按量计费」）。

### 4.3 档位映射表

| 池子内部档位 | 上报档位 |
|---|---|
| free | free |
| reserve | user |
| user | user |
| metered | paid |
| 缺失、空值、未知值 | 未定档 |

- 用户 key 清单新追加的模型上报 user（与已有同名 id 冲突时保留已有档位）。
- **缺失 / unknown 绝不归为 free。** 映射是白名单：查不到就落到「未定档」，不做默认值的猜测。
- **档位未定不算免费，也不算按量**：它既不会进 `paid_channels`，也绝不能被读成 free。

### 4.4 为什么对「未定档」这么较真

平台历史上真吃过这个亏：`codebuddy` 的 `minimax-m3.1-flash` 实际是按量付费的额度，却被声明成 free，平台于是把它当不花钱的额度放心用；2026-10-29 的一次演练里，这条模型实际吃了 98 次请求、约 1010 万输入 token（其中 opencode 一个窗口就占 850 万），而操作者一直以为是免费档，钱就这么花出去了。**「免费」是声明，平台自己不查价；声明错了，下面每一层都会照着错。** 所以现在它被标成计量（metered）；而任何没有明确声明、或声明了未知值的模型，只能记「未定档」，绝不能写成 free。

### 4.5 文件不存在与坏 JSON 的区别

| 情况 | 结果 |
|---|---|
| `pool/config.json` **不存在** | 没有额外声明，采用 `DEFAULT_CLI_SLOTS`；**不是读失败**，不退出 2 |
| `pool/config.json` 存在但坏 JSON / 顶层不是对象 / `cli_slots` 结构非法 | 读不到池子声明，额度面 `checked=false`，**退出 2** |
| `user-keys/index.json` **不存在** | 还没有给平台任何 key，模型清单为空；**不是读失败** |
| `user-keys/index.json` 存在但坏 JSON / 不是对象 / 读不了 | 读不到用户 key 清单，额度面 `checked=false`，**退出 2** |

### 4.6 本面检查了什么 / 没检查什么

检查了池子声明、用户 key 的档位与窗口在用模型；**没检查**调用模型、探测上游、核对账单。它回答的是「按当前声明的档位，有没有哪条通道会花按量的钱」，不是「这个月花了多少钱」。

---

## 5. C 账本面：有没有卡住的活？

结论问题：**账本上有没有卡住的活？** 答案由 `stuck` 与超期、未处理失败、放弃任务三项给出。

### 5.1 数据来源

| 表 | 用途 |
|---|---|
| `tasks` | 任务状态分布、`current_attempt_id` |
| `task_attempts` | 尝试状态分布、失败尝试 |
| `reviews` | 评审状态与 `lease_until`，判超期 |
| `verification_receipts` | 验证回执计数 |
| `acceptance_receipts` | 验收回执计数 |
| `task_events` | 事件计数 |

### 5.2 各项数字的准确口径

- `task_status_counts` / `attempt_status_counts`：按真实状态字面量做的分布计数（空值记为空串），按状态名排序。
- `awaiting_review`（**待评审积压**）：`tasks.status` 等于字面量 `awaiting_review` 的任务数。`reviews` 表没有 pending/requested 态——它创建即 `claimed`，所以真正的「待评审」看 `tasks`，不看 `reviews`。
- `overdue_reviews`（**超期评审**）：`reviews.status` 等于字面量 `claimed`，且 `lease_until` 能解析成时间、且**早于当前 UTC 时间**。`lease_until` 为空或解析不了一律不计超期（不猜）。
- `reviews_total`：`reviews` 表的行数。
- `failed_attempts`：`task_attempts.status` 等于字面量 `failed` 的条数（全部失败尝试）。
- `failed_unhandled`（**仍未处理的失败**）：失败尝试中，任务存在、任务状态不在终态集合（`merged`、`cancelled`、`abandoned`），且 `tasks.current_attempt_id` 仍指向该尝试的条数。任务不存在或已终态的不算「未处理」。
- `abandoned_tasks`：`tasks.status` 等于字面量 `abandoned` 的任务数。
- `verification_receipts` / `acceptance_receipts` / `events`：三张表各自的 `COUNT(*)`。
- `stuck`（**卡住的活**）：`overdue_reviews > 0` **或** `failed_unhandled > 0` **或** `abandoned_tasks > 0`。三者任一成立即「有卡住的活」。

注意：**待评审积压本身只报数，不单独算卡住，也不单独产生发现。** 积压要变成「卡住」，看的是超期评审（`overdue_reviews`）；另有未处理失败或放弃任务，也各自算卡住。

### 5.3 发现

| 代码（`findings` 里 `code` 的值） | 触发条件 |
|---|---|
| overdue_review | `overdue_reviews > 0`，消息报条数 |
| failed_unhandled | `failed_unhandled > 0`，消息报条数 |
| abandoned_task | `abandoned_tasks > 0`，消息报任务数 |

`awaiting_review`、`failed_attempts`、`reviews_total` 等只进数字，不直接触发发现。

### 5.4 本面检查了什么 / 没检查什么

检查了任务、尝试、评审、回执与事件的计数和超期；**没检查**重放事件，也没有判断某条活该不该继续。

---

## 6. 读失败的完整口径

| 场景 | 表现 |
|---|---|
| `state.db` 不存在、快照建不起来、只读打开失败 | 窗口面、额度面、账本面都 `checked=false`；`error` 非空；退 2 |
| 某张表查不了（迁移没跑、表缺失、SQL 错） | 用到该表的面 `checked=false`；其余面照常；退 2 |
| `pool/config.json` 坏 JSON / 结构非法 | 额度面 `checked=false`；退 2 |
| `user-keys/index.json` 坏 JSON / 读不了 | 额度面 `checked=false`；退 2 |
| 池子模式窗口需要档位索引，而声明或用户 key 读不到 | 窗口面也 `checked=false`；退 2 |
| 某面出现未预期异常 | 该面 `checked=false`，`error` 写「读不到<面名>的体检结果（异常类型: 信息）」；其余面照常；退 2 |

三条不变式：

1. **读失败 ≠ 没有数据。** 读不到绝不会退化成 `0` 或空表结论；
2. **单面读失败不拖垮其它面。** 失败面给空结构并标 `checked=false`，其余面照常产出；
3. **2 优先于 1。** 只要 `error` 非空就退 2，即使 `findings` 里还有别的面的真实发现。

---

## 7. `--json` 最小 schema

顶层只有六个键，**顺序固定**（`JSON_TOP_FIELDS`）：

| 键 | 类型 | 含义 |
|---|---|---|
| ok | bool | 仅当退出码为 0 时为 true |
| findings | list[dict] | 三面发现合并后的列表 |
| windows | dict | A 窗口面结果 |
| budget | dict | B 额度面结果 |
| ledger | dict | C 账本面结果 |
| error | list[str] | 读失败消息（带「窗口面：」等面名前缀）；空列表表示无读失败 |

`findings` 列表项（`JSON_ITEM_FIELDS` 的 `findings`）：

| 键 | 类型 | 含义 |
|---|---|---|
| face | str | 发现所属面：`windows`、`budget`、`ledger` 三者之一 |
| code | str | 发现代码（见 3.5、4.2、5.3） |
| message | str | 中文描述 |

`windows` 子结构（`JSON_FACE_FIELDS` 的 `windows`）：

| 键 | 类型 | 含义 |
|---|---|---|
| checked | bool | 本面是否真的读到数据；读失败为 false |
| usable_count | int | `usable=true` 的窗口数 |
| zombies | list[str] | 僵尸窗口的 `harness_id` |
| windows | list[dict] | 每个在册窗口一项 |

`windows.windows` 列表项（`JSON_ITEM_FIELDS` 的 `windows.windows`）：

| 键 | 类型 | 含义 |
|---|---|---|
| harness_id | str | 窗口唯一标识 |
| name | str | 当前登记版本的显示名；没有时退回 `harness_id` |
| live | bool | presence 快照判定是否在场（90 秒口径） |
| usable | bool | 是否满足全部接活条件（`reasons` 为空） |
| reasons | list[str] | 不可接活的原因代码列表（见 3.4） |

`budget` 子结构（`JSON_FACE_FIELDS` 的 `budget`）：

| 键 | 类型 | 含义 |
|---|---|---|
| checked | bool | 本面是否真的读到数据 |
| models | list[dict] | 合并后的模型清单 |
| paid_channels | list[str] | 档位为 paid 的模型 id |

`budget.models` 列表项（`JSON_ITEM_FIELDS` 的 `budget.models`）：

| 键 | 类型 | 含义 |
|---|---|---|
| id | str | 模型 id |
| tier | str | 上报档位：free / user / paid / 未定档 |
| owners | list[str] | 在用的窗口 `harness_id`；没有则为空列表 |

`ledger` 子结构（`JSON_FACE_FIELDS` 的 `ledger`）：

| 键 | 类型 | 含义 |
|---|---|---|
| checked | bool | 本面是否真的读到数据 |
| task_status_counts | dict[str, int] | 任务状态分布 |
| attempt_status_counts | dict[str, int] | 尝试状态分布 |
| awaiting_review | int | 待评审积压（`tasks.status` 为 `awaiting_review`） |
| overdue_reviews | int | 超期评审数 |
| failed_attempts | int | 失败尝试总数 |
| failed_unhandled | int | 仍未处理的失败尝试数 |
| abandoned_tasks | int | 已放弃任务数 |
| reviews_total | int | 评审总数 |
| verification_receipts | int | 验证回执数 |
| acceptance_receipts | int | 验收回执数 |
| events | int | 事件数 |
| stuck | bool | 是否有卡住的活 |

schema 的三条约定：

1. **字段名不多不少**：顶层、各面子结构、列表项都只有上表列出的键，没有额外键；顺序与常量表一致。
2. **读失败时结构仍在**：任何面读失败，都返回该面的默认空结构（`checked=false`，数字为 0，列表为空），顶层六个键齐全；`ok=false`、`error` 非空。体检自身连一个面都跑不起来时，也返回同样形状的单文档，绝不吐半截 JSON。
3. **`checked` 是诚实标记**：`checked=false` 表示「本面没读到数据」，不是「本面读到了零」。下游统计不要把 `checked=false` 的 0 当成真实的 0。

字段名可逐条对照 `tools/doctor/doctor.py` 的 `JSON_TOP_FIELDS` / `JSON_FACE_FIELDS` / `JSON_ITEM_FIELDS`；也可以实跑一次 `python tools/doctor/doctor.py --json`，把输出的键与上表机器比对（读失败时的默认空结构同样包含全部顶层键与各面子键）。三条列表项的字段（`findings`、`windows.windows`、`budget.models`）只在对应列表非空时出现在实际输出里；列表为空时，以 `JSON_ITEM_FIELDS` 常量表为准——实际输出与常量表是同一份定义，不存在两套。

---

## 8. 只读与零额度的保证

- 不派单、不调模型、不跑发现逻辑；
- 不新增第三方依赖，只读平台已有的代码与数据；
- 对真实 `data_dir` 零写入、零边车（只读快照见 2.2）；
- 不起任何服务、不监听端口、不访问网络。

额度面用到的 `app.pool` 子模块（`app.pool.user_keys`、`app.pool.sources`）是延迟导入：某一份依赖起不来，只让用到它的面报「读不到」，不会让整份体检起不来。在场阈值 `app.adapter.presence.PRESENCE_FRESH_SECONDS` 则是模块顶部直接导入的常量；这份依赖起不来属于工具自身无法启动，不在三面「读失败」口径之内（三面口径以工具已经跑起来为前提）。

---

## 9. 自测与复现（假数据注入）

体检没有内置测试入口；若要给它写测试或手工复现，遵守下面几条，**绝不碰真平台**：

1. **假数据注入**：在临时目录里建一份最小的 `state.db`（`harnesses`、`harness_registrations`、`harness_presence`、`auth_tokens`、`tasks`、`task_attempts`、`reviews`、`verification_receipts`、`acceptance_receipts`、`task_events` 等按需建表），再按需放 `pool/config.json` 与 `user-keys/index.json`。所有结论都应能从这几份假数据推出来。
2. **monkeypatch `settings.data_dir`**：把 `app.config.settings.data_dir` 指到临时目录；体检只认 `data_dir/state.db`、`data_dir/pool/config.json`、`data_dir/user-keys/index.json`。测试若同时维护 `settings.db_path`，两者必须指到同一处，别让一个真一个假。也可以在进程外通过环境变量 `AP_DATA_DIR` 指到临时目录。
3. **不碰真平台**：不读真实 `data_dir`、不调模型、不派单、不访问网络；不要因为「只是看一下」就把测试指向本机运行库。
4. **用 `python -B` 或设 `sys.dont_write_bytecode = True`**：避免在仓库里写下 `__pycache__` 之类的字节码副产物；跑完检查工作树应当干净。
5. 进程内调 `main(["--json"])` 可以避免起子进程；断言返回的退出码与 stdout 的 JSON 文档即可，不要依赖 stderr 的内容。

---

## 10. 评审对照速查

| 要核实的点 | 去哪里看 |
|---|---|
| 顶层/各面/列表项字段名 | 本文第 7 节；`JSON_TOP_FIELDS`、`JSON_FACE_FIELDS`、`JSON_ITEM_FIELDS`、`JSON_SCHEMA` |
| 两条命令与 stdout 约定 | 第 1 节；`main()` |
| 退出码判据与优先级 | 第 1.3 节；`_exit_code()`、`_check()` |
| 在场阈值 | `app.adapter.presence.PRESENCE_FRESH_SECONDS`（90 秒） |
| 窗口判定与原因代码 | 第 3 节；`_check_windows()`、`REASON_*`、`REASON_TEXT` |
| 额度合并与档位映射 | 第 4 节；`_budget_model_index()`、`TIER_MAP`、`TIER_*` |
| 账本判据与卡住定义 | 第 5 节；`_check_ledger()`、`_TERMINAL_TASK_STATUSES`、`_AWAITING_REVIEW_STATUS`、`_CLAIMED_REVIEW_STATUS`、`_FAILED_ATTEMPT_STATUS` |
| 快照与只读 | 第 2.2 节；`_snapshot()` |
| 读失败口径 | 第 6 节；`ReadError`、`_run_face()`、`_capture()` |

一句话收尾：**体检只负责把事实读准、把「读不到」和「读到零」分清楚；它不替人花钱，也不替人拍板。**
