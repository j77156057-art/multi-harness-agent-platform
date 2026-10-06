### 违规记录：MVP-08 第三次「先合 main 后验收」（3de2115）

**事实**：Qoder 的 MVP-08（2440566，任务表/认领表状态 🔍 review）由
`3de2115`（Merge branch 'agent/local/qoder/MVP-08/a1'）合入 main，
TRAE 的验收结论于当日 21:00 才由 MVP-08-verify 补出：通过（附遗留
清单 4 项，无 P1）。这是继 MVP-03（db9bc19）、MVP-04（e656058）之后
**第三次同模式违规**，且发生时 GATE-MERGE-CHECK-01 正在实施中、闸尚未
合入 main——红灯仍无阻断后果。

**已采取的纠正**：TRAE 以 MVP-08-verify（`agent/local/trae/MVP-08-verify/a1`）
按 DOC-TRACE-001 / MVP-API-001 两条 test_id 独立核查（不采信卡片自陈），
实跑 docstate 90/0、pytest 111 项退出 0、ruff 全过、prep 契约核对
FAILURES: 0、变异测试 10/10、真实 app.main:app 冒烟（含零 CORS 头与
Host 头拒绝），结论与 4 项遗留（错误码越表 2 项、HTTP 状态不符 2 处、
消息全局 seq/refs schema 缺口、脚手架迁移待 MVP-05/06 替换）已回填
MVP-08 卡；本记录为第三条留档。

**根因与终局措施**：根因与前两次相同——合并前没有可机械判定的验收闸，
规矩只在文档里。与前两次不同的是，本次违规被 GATE-MERGE-CHECK-01
的 merge 组在其分支上**当场捕获**：discipline 枚举 git 已合并任务分支，
对 3de2115 报出「未完成=['MVP-08']」，且该检查 fail-closed（非 git 工作区/
浅克隆即失败）。本验收关单后该红灯消除，GATE 分支随即 32/32 全绿并合入；
此后第四次同模式违规将在合并前被本地门禁与 CI（fetch-depth: 0）同时
拦截。残余盲区：直提 main 不产生合并提交，仍只能靠分支保护（B 方案，
暂未启用）与事后留档兜底。

### MVP-08：REST API、群聊与消息事件（✅ 已验收 2026-10-05 21:00，TRAE 代码核查，通过附遗留清单）

~~~text
任务编号：MVP-08
负责人 Harness：Qoder
开始时间：2026-10-05 19:20
依赖任务：MVP-02（✅ 已验收）、MVP-03（已合入 main `db9bc19`，claimed 待验收）、
          MVP-04（已合入 main `e656058`，review）
          ★ 依赖判据同 MVP-04：以「已合入 main 的代码」为准。开工前已核对 main 上
            app/claim/**、app/lease/**、app/state/**、app/spec.py 的实际接口。
工作区：.worktrees/qoder/
真相源引用：R10 §7（Adapter 接口，16 个动词）、§21（planner/review 动词）、
            §8（canonical schema）、§11（Claim 与幂等，R10-fix-09/10 口径）、
            §15（事件与全局 seq）、§19（群聊与 read_context）、§5（只绑 localhost）、
            §10（全部状态迁移必须经 MVP-03 迁移表）、§26（检查 ID）
            契约与错误码：docs/prep/MVP-08/（本任务 own 的 prep，开工时按当时 R10 复验）
允许修改目录：backend/app/api/**、backend/app/tasks/**、backend/app/main.py、
            backend/tests/**
禁止修改目录：docs/spec/0.3-R10.md、tools/docstate/**、tools/discipline/**、
            backend/app/db/**、backend/app/events/**、backend/app/state/**、
            backend/app/plan/**、backend/app/claim/**、backend/app/lease/**
交付分支：agent/local/qoder/MVP-08/a1
完成标准：
  1. 只通过 API 能跑完「建群 -> 发方案 -> 人类批准 -> 领取 -> 提交 -> 审查」，
     全程不直接碰数据库（清单 MVP-08 的原始完成标准）
  2. 任务与事件查询支持 since_seq 增量，且同一次请求返回的 seq 连续、带 next_seq
  3. 统一错误码：每个错误响应带 error.code / retryable / 可选 seq，code 取自
     docs/prep/MVP-08/error-codes.md；幂等重放带 Idempotency-Replayed
  4. 群聊消息与 task_events 共享同一个全局 seq 空间，@mention 落 message_deliveries
  5. 服务只绑定 loopback，且不返回任何 CORS 头
test_id：DOC-TRACE-001、MVP-API-001
测试命令：./scripts.sh check
当前状态：✅ 已验收（2026-10-05 21:00，TRAE 独立核查，结论与证据见卡末「验收意见」）
完成时间：2026-10-05 19:54
commit：2440566
验证结果：
  docstate    : 90 项检查，0 项失败
  discipline  : 24 项，1 项失败（缺卡=['fix-flaky-init']，WorkBuddy 的任务，非本任务）
  ruff check  : All checks passed
  ruff format : 40 files already formatted
  pytest      : 107 passed（MVP-08 新增 22）
  冒烟        : 对真实 app.main:app（含启动时 docstate 90/90）跑通
                /health、/spec/status、建项目/建群/发方案/批准/领取/查任务/查事件
  ⚠️ ./scripts.sh check 在 dirty main 上仍会在 discipline 处短路，ruff 与 pytest
     是用 ./scripts.sh lint / ./scripts.sh test 单独跑的。
已知问题：
  - **清单 MVP-08 的完成标准按字面不可达，已按实际可达范围交付并登记**：
    「只通过 API 跑完 建群→发方案→领取→提交→审查」这条链路上有两个迁移不属本任务 ——
    `claimed → in_progress` 需要 worktree（R10 §10 guard「worktree ready」），属 MVP-05；
    `verifying → awaiting_review` 需要通过的 receipt（R10 §16），属 MVP-06。
    而 MVP-08 在依赖图里排在 MVP-05/06 之前。测试里这两步由 SchedulerService 的
    `start_task` / `record_verify_passed` 驱动（真实迁移、真实事件），**未伪造任何
    receipt**，两个方法都写了 docstring 说明它们是脚手架且不出现在 HTTP 面上。
    docs/traceability.md 的 `MVP-API-001` 相应标为新增的 🟡「部分有断言」。
  - **消息与 task_events 没有共享 seq 空间，这是 schema 层缺口**：R10 §15 规定全局 seq
    是排序权威，但 `001_initial.sql` 里只有 `task_events.seq` 带 AUTOINCREMENT，且该表
    被 append-only 触发器保护（不能插一条再删来推进计数器），`task_messages.created_seq`
    是普通 INTEGER，没有任何分配器。本任务的实现给消息**按 group** 分配自己的单调 seq，
    并在 `GET /groups/{id}/messages` 用独立游标返回。要真正做到 R10 §15 的全局 seq，
    需要一个序列表（或把消息也写进事件流），两者都要动 `backend/migrations/**`
    （属 MVP-02），不在本任务允许目录内。**已在 prep 的 README §5 第 6 条预警过，
    此处是它的实测确认。**
  - 未实现、且不属本卡 5 条完成标准：`publish_task_branch`（MVP-05）、receipt 与
    review/merge Lease 的完整形态（MVP-06/07）、Harness 能力门控、`accept_help` /
    `reject_help` / `split_task` / `ask_human` 等其余 §7 动词。
  - 跨任务改动（已在此登记）：`docs/traceability.md` —— 给 `MVP-API-001` 换标记并写明
    缺口，同时在「强制状态列的含义」里新增 🟡 档。该文件属 graph-traceability，其状态
    列不被断言；不改会让同一提交里的代码与文档相互矛盾。

验收意见（TRAE，2026-10-05 21:00，基线 e6b3026；不采信卡片自陈，逐条找代码行+测试断言）：
  总评：✅ 通过（附遗留清单 4 项，均 P2/P3，无 P1）。核心不变量——fencing、单一迁移
  表、幂等、loopback——都有机械负向测试；两处脚手架迁移诚实、未伪造 receipt。

  DOC-TRACE-001（契约↔R10 可追溯）✅：
  - 路径命名与 R10 §7/§21 逐字对齐（app/api/routes.py：claim/heartbeat/
    submit_result/request_help/report_blocked/release/abandon、review/submit）；
  - docs/prep/MVP-08/check_contract.py 实跑 FAILURES: 0（openapi/prep 是
    §7/§8/§10/§11/§21/§37.7 的忠实投影）；mutation_test.py 实跑 10/10，
    证明契约守卫非空转；
  - 错误包络 code/message/retryable 形状有 test_api_errors.py 10 例钉死，
    且验证错误只回字段名不回值（D-8，断言 "42" 不出现在响应体）。

  MVP-API-001（全链路仅经 API）🟡 维持，验收确认口径：
  - HTTP 面：建群/发方案/批准（含依赖拆分 ready vs ready_waiting_deps）/领取/
    心跳/提交/审查（approve 与 request_changes 两分支、review_round 推进、
    last_review_id 落库）全部经 TestClient 机械断言（test_api_flow.py 4 例）；
  - 两处非 HTTP 迁移（claimed→in_progress、verifying→awaiting_review）由
    SchedulerService.start_task / record_verify_passed 驱动，走真实迁移表与
    真实事件，docstring 明示为脚手架且不出现在 HTTP 面，无伪造 receipt——
    与 R10 §10 worktree guard、§16 receipt 串行要求不冲突，归属 MVP-05/06；
  - 事件游标：since_seq 排他、分页 next_seq/has_more、seq 连续有序、按任务
    过滤（test_api_events_messages.py 8 例）；@mention 落 message_deliveries
    有直接 SQL 断言；
  - 安全：默认绑 127.0.0.1（config.py:28，AP_HOST 改 0.0.0.0 注释要求重新评审，
    符合 §5「默认」口径）；Host 头白名单中间件拒非 loopback（400
    auth.host_header_rejected，有测试）；对真实 app.main:app 冒烟实测响应中
    零 access-control-* 头；幂等重放带 Idempotency-Replayed、同 request_id
    不同载荷 409 idempotency.conflict，均有测试。

  门禁实测（2026-10-05 21:00，e6b3026 工作区）：
  - docstate 90/0；ruff check 全过、ruff format 40 files already formatted；
  - pytest 全量 111 项（MVP-08 新增 22：flow 4 + errors 10 + events/messages 8）
    退出码 0；check_contract.py 0 FAILURES；mutation_test.py caught 10/10；
  - app.main:app 冒烟（临时 AP_DATA_DIR）：health/project/group/plan/approve/
    claim/events/message 全 200，CORS 头为空，伪造 Host 400。

  遗留清单（不阻塞本验收；1/2 是 Adapter 接口契约问题，须在 MVP-09/10a
  对接错误码之前处理）：
  1. 两个错误码不在 error-codes.md 封闭集合内（违反该 prep §0.1）：
     errors.py 使用的 `claim.concurrent_claim`（ConcurrentStateChange 映射）
     与 `request.conflict`（通用 Conflict fallback，涵盖资源已存在/状态不符/
     非 draft 批准等多处）。修法二选一：映射到表内码（并发写竞争语义接近
     claim.concurrency_full 但需裁定；通用冲突按场景映射 state.transition_undefined
     或资源专属码），或按 §0.1 走 prep 复审把缺口码补登并附 R10 引用；
  2. 两处 HTTP 状态码与表不一致：review.self_review_forbidden 表定 403、
     实现发 409（test_api_errors.py 也钉了 409，修时同步改测试）；
     request.invalid 表定 400，但服务层 InvalidInput 发 422（plan 语义校验、
     空消息体）。建议语义校验改用专属码（verdict→review.verdict_not_allowed、
     plan linter→plan.*），或在表内为 422 补一个通用语义码；
  3. 消息能力两处 schema 级缺口（卡片已自陈、traceability 已标 🟡，确认属实）：
     a) ✅ 已关闭（message-global-seq，2026-10-05）：迁移 003 建单行表
        global_seq 统一发号、task_events 去 AUTOINCREMENT 重建，消息与事件
        同事务取号；两流交错连续、跨群不重号、存量库续号、回滚不空洞均有
        机械断言（test_api_events_messages.py、test_database_events.py）。
        原始问题存档：消息 seq 曾按 group 独立分配（MAX(created_seq)+1），
        不与 task_events 共享全局 seq——R10 §19 read_context(since_seq)
        未来要混合排序消息与事件，需序列表或消息入事件流，均要动 migrations
        （MVP-02 范畴）；checklist MVP-08 的交付条目原文只要求「消息与
        task_events 关联」（task_id 列已做），故不阻塞；
     b) ✅ 已关闭（message-refs-persist，2026-10-05）：迁移 004 为
        task_messages 增加 refs 列（JSON TEXT NOT NULL DEFAULT '[]'），
        post_message 校验（list[str]、空串拒绝）后同事务落库，
        list_messages 解码为数组回显；存量行回填 []。POST→GET 往返、
        缺省 []、非法值 400/422、旧库升级回填均有机械断言。
        原始问题存档：PostMessage 曾接受 refs 但无列、service 接收即丢、
        GET 不回显（仅 POST 响应原样回显），契约静默说谎。
  4. MVP-05/06 落地后，须把 test_api_flow.py 里两处 service.start_task /
     record_verify_passed 驱动替换为真实 HTTP 路径（publish_task_branch、
     Verify Worker receipt），届时 MVP-API-001 方可由 🟡 转 ✅。归属：MVP-06
     验收时检查，或单开收尾任务。
~~~
