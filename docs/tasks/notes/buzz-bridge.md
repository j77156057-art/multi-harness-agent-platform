### buzz-bridge：平台事件 → Buzz 频道（单向发布运行时）（🚧 doing）

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
当前状态：doing
~~~
