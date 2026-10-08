### buzz-bridge：平台事件 → Buzz 频道（单向发布运行时）（✅ done）

~~~text
任务编号：buzz-bridge
负责人 Harness：Qoder
开始时间：2026-10-09 03:10
依赖任务：buzz-bridge-adr（✅ 556511e；ADR-DEV-005 是唯一授权与约束来源）；
          alert-webhook（✅；游标/重试/no-op 先例，`alert_cursors` 形状）
工作区：.worktrees/qoder/
真相源引用：ADR-DEV-005（全部决策条款：只写不读、DB 游标源、kind:9+h tag+
            NIP-42、消息=投影、至少一次+哈希去重、未配置 no-op、回退铁律）；
            报告 §1（kind:9 必须带 `["h", channel]`；WS+NIP-42 实证；buzz-sdk
            不持钥）与 §5 候选 a；R10 §17（凭据受控注入、最小披露）；§37.7
            （不新增事件名）
范围声明：
  - **本切片（按 ADR 逐条实现）**：
    ①迁移 013 `buzz_cursors(stream PK, cursor_seq, updated_at)`（形状抄
      `alert_cursors`）。
    ②`app/buzz/nostr.py`：NIP-01 事件构造/序列化/id（sha256）/签名
      （coincurve libsecp256k1 BIP340——开工前已用 BIP340 官方向量 #0
      交叉验证 `sign_schnorr` 逐字节吻合）；NIP-42 AUTH 事件（kind:22242 +
      relay/challenge tag）；PublicKeyXOnly 验签供测试 relay 用。
    ③`app/buzz/projection.py`：事件 → 展示消息（字段白名单渲染：task_id/
      group/event_name/next 状态/actor/时间/一行摘要；**不转发 payload**）；
      默认发布集=状态变迁 M 事件 + `human.*` + 安全事件（`permission_denied`/
      `secret_scan_blocked`），观测类 O 事件默认静默（常量集 + 测试对白名单
      关系断言）。
    ④`app/buzz/client.py`：WS 发布客户端（websockets 库）：连接 → 收 AUTH
      challenge → 发 NIP-42 AUTH → 等 OK → 逐条发 pending 事件（kind:9 +
      `h` tag）→ 收每条 OK 才算成功；超时/断连抛错给游标层。
    ⑤`app/buzz/bridge.py` + `__main__.py`：按游标增量读、过滤、投影、发布；
      游标仅在整批 OK 后推进（至少一次 + Nostr id 去重）；`--check`（配置+
      游标+next 候选，exit 0/2）、`--once`、`--loop`（AP_BUZZ_INTERVAL 默认
      10s）；**未配置=显式 no-op（exit 0）**，同 alerts 先例。
    ⑥配置：`AP_BUZZ_RELAY_WS` / `AP_BUZZ_CHANNEL` / `AP_BUZZ_KEY_FILE`
      （私钥=受控凭据文件，不进库/仓库/事件/日志）；requirements 显式钉
      `websockets==17.2`（uvicorn[standard] 的传递依赖提为直接使用）+
      `coincurve==21.0.0`。
    ⑦测试 `test_buzz_bridge.py`：**测试内自建最小 relay（websockets 服务端，
      真 WS）**——发 AUTH challenge、强制 AUTH、验 event id 重算+schnorr 验签
      （PublicKeyXOnly）+`h` tag、按 id 去重、回 OK；覆盖：单轮发布到 relay
      全链、顺序、投影不含 payload 字段、游标推进与二轮 no-op、relay 挂了
      游标不动且下一轮重试（relay 去重）、BIP340 向量、投影白名单关系、
      CLI 三态（--check 未配置/已配置、--once、坏配置 exit 2）。
    ⑧**验收（协调者确认：本地自建 relay）**：测试内 relay 端到端 +
      `backend/data/buzz-drill/` 起独立 relay 进程、真 CLI `python -m
      app.buzz --once` 对真库发布并留档收到的消息。
  - **显式出范围**（记录不静默；ADR 允许项）：NIP-10 e-tag 线程 v1 用**平铺
    降级**（ADR 决策 4 的降级条款：本地 relay 无法验证真实 Buzz 的线程语义；
    平铺 = 频道内消息 + 文本前缀含 task_id；e-tag 待真实 Buzz relay 可达后
    补）；HTTP bridge（NIP-98）不用；NIP-34/DM/workflow 全在闸外；节流/摘要
    策略=先如实跑、量大了再做（ADR 回退条件 3 的处置）；真实 Buzz relay 的
    实机验证仍需协调者提供地址（本卡验收=本地 relay，范围内闭环）。
  - **只写不读**：客户端不 REQ、不订阅；代码里没有任何入站处理路径（结构上
    杜绝第二事实源）。
允许修改目录：backend/migrations/013_buzz_cursors.sql（新增）、
            backend/app/buzz/**（新）、backend/app/config.py、
            backend/requirements.txt、backend/tests/**、README.md（一小节）、
            TASKS.md、docs/traceability.md、docs/tasks/notes/buzz-bridge.md、
            docs/claims/qoder--buzz-bridge.md
禁止修改目录：backend/app/state/**、docs/spec/**、docs/adr/**（ADR 是约束
            来源，不改）、frontend/**、tools/**、backend/app/alert*/**（只抄
            形状不耦合）
交付分支：agent/local/qoder/buzz-bridge/a1
完成标准：
  1. 端到端（测试内真 WS relay）：一批平台事件 → 桥 → relay 收到 kind:9 +
     `h` tag + 合法签名 + 重算 id 一致的 NOSTR 事件；消息顺序=seq 顺序；
     投影只含白名单字段（构造含敏感样式 payload 的事件，断言其值不出现在
     消息里）
  2. 游标语义：成功后推进；relay 不可达/拒绝 → 游标不动、报错可见；下一轮
     重试，relay 按 event id 去重（至少一次+去重=恰好一次的显示效果）
  3. 配置三态：未配置=no-op（exit 0，不连 WS）；已配置+relay 在=发布成功；
     坏配置（key 文件缺失/非法）=exit 2 明确拒绝
  4. NIP-42：relay 强制 AUTH——未 AUTH 的 EVENT 被拒；桥总是先 AUTH 并在
     测试里断言 AUTH 事件形状（kind:22242+relay/challenge tag）
  5. BIP340 官方向量交叉验证（sign_schnorr 逐字节）+ 事件 id 重算一致
  6. 真实 CLI 验收（本地自建 relay，协调者确认的通道）：独立 relay 进程 +
     真库 `--once` 发布，留档收到的消息与游标
  7. `PYTHONUTF8=1 ./scripts.sh check` 全绿；traceability 新增
     `INTEG-BUZZ-001`
test_id：INTEG-BUZZ-001（新登记）
测试命令：PYTHONUTF8=1 ./scripts.sh check
当前状态：done（已合入 main）
完成时间：2026-10-09 03:35（实现）；2026-10-09 03:52 合并（aad2b3f）
commit：实现 `219ec4e`（登记 `ffb6d26`）；合并 `aad2b3f`（mergeguard 门禁全
        绿）；合并后 main 全量门禁 `529 passed`
验证结果：
  docstate    : 90 项检查，0 项失败
  discipline  : 44 项检查，0 项失败
  ruff        : All checks passed
  pytest      : 529 passed（本任务净增 16，`test_buzz_bridge.py`），exit 0
  交付物      : `migrations/013_buzz_cursors.sql`、`app/buzz/{__init__,nostr,
                projection,client,bridge,__main__}.py`、`config.py` 的
                `AP_BUZZ_*` 五项、`requirements.txt` 显式钉 websockets/coincurve、
                README 一小节
  测试要点     : 测试内真 WS relay（NIP-42 challenge → 强制 AUTH → 重算 id +
                schnorr 验签 + `h` tag → 按 id 去重）跑全链；投影白名单对
                §37.7 M/O/R 做集合关系断言（M=74/O=14/R=1，发布集 = M −
                {escalation_reminder} + {permission_denied,
                secret_scan_blocked}）；payload 含 "SECRET-XYZ" 的事件发布后
                断言该值不出现在消息里；BIP340 官方向量 #0 逐字节（aux=32 零
                字节，与 k 文件无关）；「relay 挂→游标不动→重启重试→重放同
                id→relay 去重」闭环；强制 AUTH 有对照组（裸连接不发 AUTH 的
                EVENT 被拒且不落账）；CLI 三态 + 部分配置/坏 key exit 2。
  本地自建 relay 真验收（协调者确认通道；`backend/data/buzz-drill/`，
  gitignored，脚本可复跑）：
    ① 独立 relay 进程（`relay_server.py`，写 `relay-messages.jsonl` 留档）
       + 真 CLI `python -m app.buzz`（`AP_DATA_DIR` 指向演练 scratch 库）；
    ② 序列：relay 关 → `--once` **exit 1**（`ConnectionRefusedError`，
       cursor 0、pending 4）；起 relay → `--check` exit 0（cursor 0、pending 4、
       next 4 条）→ `--once` exit 0（published 4、cursor 6）→ `--once` exit 0
       （scanned 0、published 0，纯 no-op）；
    ③ 追加 1 条 submit_result → `--once` published 1（relay transcript 第 5 条）；
    ④ 演练库把游标倒回 0（仅 scratch）→ `--once` 重放 5 条 → relay 逐条标
       `duplicate=true`：transcript 10 行 EVENT = 5 唯一 id + 5 重复，
       **显示效果恰好一次**；
    ⑤ `relay-messages.jsonl` 中 5 条消息的 content 均无 "SECRET-XYZ" 泄露；
       AUTH 事件 pubkey 一致、accepted=true。
  演练所得      : 抓到并修掉一个真 bug——空批次分支 `scanned` 误报 1（改 0，
                测试补断言）；另发现演练 relay 脚本在 Windows 上逐行
                open/append 会撞瞬时文件锁（改长句柄 + flush；**非应用代码
                bug**，桥本体的 1011 中断被游标层正确兜住——游标未动、
                exit 1，恰是演练要证的行为）。
已知问题/披露：
  - **死信停靠表未建**（对 ADR 决策 6 的取舍）：源=事件流本身即队列，
    "游标不动"即重试；积压在 `--check` 的 pending 里可见。若未来需要"放弃
    积压/自动停靠"，另开卡。
  - **NIP-10 e-tag 线程 v1 用平铺降级**（ADR 决策 4 的降级条款）：消息文本
    自带 task_id 前缀；真实 Buzz relay 可达后补 e-tag。
  - `created_at` 取**源事件时间**（非发布时刻）：同 seq 重放 → 同 id 同签名的
    前提；显示时间是平台事实时间。
  - ADR-DEV-005 状态仍为**提议**；本卡按已登记约束实现，状态翻转待协调者。
  - **依赖新增**：`coincurve==21.0.0` + `websockets==17.2`（requirements 已
    钉）。合并后各 worktree 的既有 venv 需各跑一次
    `pip install -r backend/requirements.txt`，否则 pytest 收集期
    `ModuleNotFoundError: coincurve`（root venv 已代为更新；合并后 main 门禁
    因此从红转绿的前后两跑都留档在案）。
~~~

## 结论

Buzz 桥 v1 闭环：单一真相源不破（只写不读，结构上没有入站路径），
显示层拿到的是**投影**而非事件转发，断链、重放、重复投递三个失败面都有
对照验证。真实 Buzz relay 的实机验证仍需协调者给地址——那是本卡之外的
最后一步（ADR 决策 9 的诚实缺口仍在）。
