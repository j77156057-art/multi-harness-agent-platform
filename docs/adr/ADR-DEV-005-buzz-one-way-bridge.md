# ADR-DEV-005：Buzz 单向桥——事件发布到 Buzz 频道

状态：**提议（Proposed）**——待协调者裁决后转「已接受」
日期：2026-10-09
关联任务：buzz-bridge-adr（协调者「先立 ADR」指令；路线图第 5 项的前置）
影响范围：未来 `backend/app/buzz/**`、一张游标表（`buzz_cursors`，形状同
          `alert_cursors`）、`AP_BUZZ_*` 配置、Buzz 侧一个 agent 身份与目标频道；
          **本 ADR 本身零代码、零迁移**

---

## 背景

路线图第 5 项（协调者原文）：*「将平台 JSONL 事件发布到 Buzz 频道。Buzz 只
作为沟通和展示层，R10 继续作为唯一业务真相源。」*

两道既有的闸决定了这件事必须先写成 ADR，而不是直接开工：

1. **ADR-DEV-003 决策 4**：引入外部运行时或协议必须另立 ADR 并经协调者裁决；
2. **ADR-DEV-004** 把首个集成面定为 JSONL 导出（只读），并把
   *「Nostr relay 或任何外部协议运行时」* 显式留在闸外——本 ADR 就是就那一项
   做的第一个（也是最小的）裁决：**只发布、不回读**。

源码核实给了这条路一个现成而低风险的形状（`paperclip-buzz-source-review.md`
§1/§5，快照 commit 已记录）：

- Buzz 的频道 = **NIP-29 group**，消息 = **kind:9** 且必须带 `["h", channel]`
  标签（`NOSTR.md:348`）；线程 = NIP-10 `e`-tag root/reply；
- 接入面（实证）：WS + **NIP-42 AUTH**（agent keypair）；`buzz-sdk`（类型化
  构造器，**不持钥**——签名在调用方）与 `buzz-ws-client.publish_event`；
  现成同款示例 = `examples/countdown-bot`（WS+NIP-42+kind:9000/9）；
- 报告 §5 候选 a（本 ADR 的授权对象）风险评级=低：只写 Buzz，不回读、不回写
  平台。候选 b（Paperclip `external-task-protocol`）**天生双向**，与单真相源
  边界冲突，仍留闸外。

## 决策

1. **授权范围**：允许实现「平台事件单向发布到 Buzz 频道」这一个外部协议运行
   时（报告候选 a）。**仍在闸外**（各自另立 ADR）：external-task-protocol 双向
   互认、作为 Paperclip adapter-plugin、NIP-34 git 族镜像（patch/PR/merged）、
   DM/NIP-17、Buzz workflow kinds、buzz-acp 宿主形态。
2. **单向铁律**：桥只读平台事件、只写 Buzz。**不订阅 Buzz、不回读入站**
   （不 REQ）。Buzz 侧的任何状态（消息 id、用户、审批、表情）都不进平台库、
   不参与任何状态判定；Buzz 不可用不影响平台任何行为。
3. **发布源 = 权威 `task_events`（DB）按全局 seq 游标增量读**，与 `app/alerts`
   同族。裁决理由：显示要的是时效（JSONL 是异步导出，天然滞后），游标推进
   要与发布结果同事务判定；JSONL 导出继续作为备份/分析/灾备面，**桥不读
   JSONL 文件**——导出与桥互不依赖，两个消费者各自对账。路线图说「JSONL
   事件」按「平台事件流（JSONL 是其导出物之一）」理解；若协调者更想要
   文件面消费，属实现卡内的一个源适配点，不改本 ADR 的其余约束。
4. **线路（wire）**：WS 直连 Buzz relay；发布 **kind:9 + `["h", channel]`**；
   **NIP-42 AUTH** 用平台持有的 agent keypair（由平台侧签名——`buzz-sdk`
   不持钥已核实，密钥管理是平台职责，见决策 7）。**任务线程**：NIP-10 `e`-tag
   root/reply（任务首条进频道的事件建 root，后续按 root 回复）；若实机验证
   目标 relay 对 `e`-tag 线程的处理与快照不符，允许 v1 降级为「频道内平铺 +
   统一前缀（含 task_id）」，降级事实写进实现卡。
5. **消息是投影，不是转发**：发出的是**渲染后的展示文本**，字段白名单
   （task_id / group / event_name / 状态迁移 next / actor / 时间 / 一句人类可读
   摘要）；**不逐字转发事件 payload**——completion/artifact/findings 可能携带
   代码与自由文本，对「发到第三方平台」这道新出口按最小披露原则重新投影。
   密钥与任何 secret 一律不出境（§17 同口径：凭据只经受控注入，不落库、不进
   事件）。
6. **幂等与游标**：Nostr 事件 id = 内容的哈希，同一 seq 的重放天然幂等（relay
   按 id 去重）；平台侧游标表（`buzz_cursors`，形状同 `alert_cursors`）只在
   发布成功后推进。语义 = **至少一次投递 + 内容哈希去重**，可观测近似恰好
   一次。重试与死信停靠沿用 alerts 族（上限 + `parked` + `--check` 退出码）。
7. **配置与默认**：`AP_BUZZ_*`（relay WS 地址、频道 id、key 文件路径）。
   **未配置 = 显式 no-op**（同 alerts「未配置显式 no-op」先例）；agent 私钥 =
   受控凭据文件（不进库、不进仓库、不进事件、不进日志）。
8. **身份与准入**：平台在 Buzz 侧以一个 agent 身份出现；所有者背书（NIP-OA）
   与准入（NIP-AA）按 Buzz 侧要求由人类完成，**不在本仓自动化**；该身份私钥
   轮换 = 运维动作。
9. **诚实缺口（实现卡必须以最小真机验证覆盖其一）**：HTTP bridge 的认证细节、
   `buzz-sdk` 签名边界的完整确认、目标 relay 对 kind:9+NIP-42+`e`-tag 的实机
   行为——报告 §7 的未证实清单在本 ADR 之后依然有效；实现沿 `countdown-bot`
   同款路径落一条真机发布，未证实部分如实标注。

## 消息映射原则（v1；具体表在实现卡细化）

- **默认发布**：状态变迁（M 集合里带 `next` 的事件——「发生了什么」的天然
  定义）+ `human.*` 全集（人类决策可见）+ 安全类（`permission_denied`、
  `secret_scan_blocked`）。
- **默认静默**：观测类 O 事件（提醒、对账、消费滞后等）与 intent 台账——
  防频道被运维噪音淹没；是否放行做配置开关。
- 映射是**展示语义**，允许摘要化；不得从 Buzz 反推平台事实。

## 后果

### 正面

- 沟通层与业务层第一次真实打通，而单一真相源不破：**只写不读**让「第二事实
  源」在结构上不可能出现；
- Nostr 的「事件 id = 内容哈希」使显示消息天然可去重、可对账（同 seq 重放 =
  同 id = relay 侧幂等）；
- 复用 alerts 族的游标/重试/停靠/`--check` 模式，运维面不新造轮子。

### 负面 / 成本

- 多一个外部依赖与一份凭据（agent 私钥的文件管理；
- 事件→消息映射是展示语义，需要节制（默认静默规则即为此），否则频道会被
  状态机噪音刷屏；
- relay 对 kind:9/线程/AUTH 的具体行为依赖 Buzz 侧版本，快照 commit 之后的
  上游变化不在覆盖范围（报告 §7 口径）。

## 回退条件

1. 若任何需求要让 Buzz 成为**输入**（哪怕「点个赞就 resume」这类）→ 停：这是
   第二事实源的苗头，另立 ADR 重评 ADR-DEV-003 边界；
2. 若 relay 对 kind:9/NIP-42/`e`-tag 的行为与快照不符，且 `countdown-bot`
   同款路径也失败 → 桥维持**未启用**，回归报告候选 d（借鉴不集成），不擅自
   换协议或自造兼容层；
3. 若消息量使频道不可读 → 先做映射节流/摘要（实现卡内），仍不可读则暂停桥；
   **不把状态语义迁去 Buzz**。

## 兼容性保证

零 schema 变更于权威面（仅新增游标表，形状抄 `alert_cursors`）；不触碰任何写
路径与状态机；不新增事件名（§37.7 白名单不动）；JSONL 导出行为不变；前端与
API 面不变。桥的默认状态 = 未配置即 no-op，因此合并本 ADR 的实现不改变任何
现有部署的行为。

## 关联

- R10 §15（事件流/导出）、§17（凭据与最小披露）、§37.7（事件白名单冻结）
- ADR-DEV-003（决策 4 的闸；Buzz 域核实事实）、ADR-DEV-004（本 ADR 兑现其
  「Nostr relay 另立 ADR」的预留）
- `docs/reports/paperclip-buzz-source-review.md` §1/§5/§7（wire 事实与未证实
  清单）、§8（证据可复现）
- `app/alerts`（游标/重试/停靠先例；`alert_cursors` 形状）
