### auth-layer：主体令牌 + API 层权限矩阵执行（✅ done）

~~~text
任务编号：auth-layer
负责人 Harness：Qoder
开始时间：2026-10-08 15:35
依赖任务：R10-fix-12（✅ 1866fad；permission_denied 已登记为 O 集合事件——
          本卡是其第一个生产者）；permission-matrix（✅ a968ac7；设计输入=
          docs/reports/permission-matrix-design-input.md）
工作区：.worktrees/qoder/
真相源引用：R10 §17（角色×操作矩阵逐行照录：approve_plan 仅人类；claim/
            heartbeat 各角色；submit_result 仅开发 Harness 自己的 attempt；
            submit_review 仅 Reviewer 自己的 review lease；publish_task_branch
            自己的任务分支或协作者 help-<n>；merge/integration write 仅 Merge
            Worker；release_escalated/pin/unpin/delete_worktree 仅人类管理员
            或明确 403；read_secret 各按 scope——v1 无面者如实标注）；§17
            L1055-1058（"Harness token 不能直接改变 owner、lease、fencing、
            receipt、主分支、方案批准和 review approval"；"所有 403 或 ACL
            拒绝都写审计事件"）；§26（DOC-AUTH-001 验收尺）；§14（分支命名=
            发布面身份判据）；permission-matrix 报告 §3（可借鉴：resource:action
            键命名 / 授权三元组 / 单点决策函数；不照搬 company/21 键/五态审批）
范围声明：
  - **本切片（A 主体与令牌 + B API 层矩阵执行）**：
    ①迁移 012 `auth_tokens`：`token_hash`（sha256，**明文永不落库**）、
      subject_type（harness|human）、subject_id、label、created_at、
      revoked_at/revoked_reason；UNIQUE(token_hash) + subject 索引。
    ②`app/auth`：`tokens.py`（issue 返回一次明文 / verify（compare_digest，
      含吊销检查）/ revoke / list——逐条状态）；`policy.py`：**单一决策函数**
      `decide(principal, permission_key, resource)`（键命名 `resource:action`，
      表逐行镜像 §17；v1 主体只有 human 与 harness 两类，R10 七角色中
      Planner 并入 human、Verify/Merge Worker 为进程面不拦、协作者=harness+
      help 面——每一项在卡内如实记录）；`__main__.py`：CLI
      `python -m app.auth issue|revoke|list`（issue 校验 harness 已在人类维护
      的注册表内，未知者拒绝）。
    ③API 接线：`require_harness` / `require_human` 依赖（`Authorization:
      Bearer`）；**身份绑定**（防冒领/身份混淆）：probe/capabilities/claim/
      ask_human/accept_help/reject_help 的声明 harness_id、review 的
      reviewer_harness_id、human/管理面的 decided_by 必须等于 token 主体；
      owner-scoped 动词（heartbeat/submit_result/request_help/report_blocked/
      split_task/release/abandon/ask_human/publish_task_branch）要求 token
      主体 == 任务 owner；publish 分支的 harness 段 == 主体（§14 判据复用）。
      ④拒绝路径：无/坏/吊销 token → 401 `auth.token_invalid`；越权或冒领 →
      403 `auth.permission_denied`；**两者都恰写一条 `permission_denied`
      审计事件**（aggregate_type="auth"，payload=key/subject/reason/method/
      path），且拒绝**零业务副作用**（不写任务状态、不写其他事件）。
    ⑤模式：`AP_AUTH_MODE=off`（默认）/`enforce`。off=零行为变化（既有 470+
      测试、演练脚本、前端在 off 下完全不动）；enforce 语义由专用测试与
      独立实例实测证明。
    ⑥次序：主体检查先于事实检查（无 token 打未知任务 → 401/403 而非 404，
      不泄露资源存在性）；对已认证且合法请求，既有事实校验语义逐字不变。
      报告建议过"先事实后主体"——本卡取主体先并在此说明分歧理由（防探测）；
      次序在测试里钉死。
  - **显式出范围**（记录不静默）：
    - **前端 token 接线**：人操作面（human-console）在 enforce 下需要携带
      human token；本卡不动 frontend——**enforce 推广到 8848 之前必须先完成
      前端接线（后续卡）**，未接线前生产保持 off；
    - **OS ACL 三账户**（报告切片 C）：ADR-DEV-002 单账户降级不变，
      DOC-AUTH-001 只升到 🟡 并把该缺口写在"由谁检查"列；
    - 协作者 help 面的工作树产出端到端（报告缺口③）只做身份绑定层，
      help worktree 全链仍缺；read_secret（缺口④）继续 N/A；
    - 进程面主体（scheduler 巡检家族/alerts/liveness/budget/exporter/
      mergeguard/runtime）不经 HTTP token——按报告建议=调度中心自身主体，
      本卡不拦（如实记录）；
    - pin/unpin、delete_worktree、merge/integration write 无 HTTP 面——
      矩阵行标注"无面"，不发明端点；
    - 读族（projects/groups/tasks/events/diff/messages 与 /adapter/
      list_ready_tasks、read_context）与群聊发言（非权威字段）保持开放，
      如实记录（§17 矩阵不覆盖读族；加读层认证会连带前端，属后续）。
    - cross_provider 强制（F5）不在本卡。
  - **迁移 012 一张**（auth_tokens）；审计复用既有 events 表（事件名
    permission_denied 已由 R10-fix-12 合法化）。
允许修改目录：backend/migrations/**（新增 012）、backend/app/auth/**（新）、
            backend/app/api/**、backend/app/config.py、backend/tests/**、
            README.md（auth 段）、TASKS.md、docs/traceability.md、
            docs/tasks/notes/auth-layer.md、docs/claims/qoder--auth-layer.md
禁止修改目录：frontend/**、docs/spec/**、tools/**、backend/app/state/**、
            backend/app/verify/**、backend/app/merge/**、其他 harness 的卡
交付分支：agent/local/qoder/auth-layer/a1
完成标准：
  1. token 生命周期：签发返回一次明文；库内仅 hash（断言明文不出现于 DB）；
     吊销后立即失效；未知/坏格式 → 401 auth.token_invalid + 审计恰一条
  2. 身份绑定反例：harness A 的 token 声明 harness_id=B → 403 + 审计；
     owner 不匹配（他人 attempt/task）→ 403 + 审计；声明与主体一致 → 放行
     （既有事实校验路径不变）
  3. 矩阵逐格（照 coverage 惯例每格至少一个反例）：harness→/human/* 与
     /plans/* 全部 403；human→/adapter/* 与 /review/* 403；同主体合法路径
     200/409（事实错）——矩阵表在测试里与 policy.POLICY 逐键对齐断言
  4. 审计恰一条且无副作用：每次拒绝 +1 条 permission_denied（payload 含
     key/reason/subject/method/path），任务状态与其他事件计数不变
  5. 模式：AP_AUTH_MODE=off（默认）时全量既有测试绿（零行为变化，pytest
     计数只增新文件）；enforce 由专用测试覆盖
  6. 次序钉死：无 token + 未知 task → 401（非 404）；有 token 无权限 + 未知
     task → 403（非 404）
  7. CLI：issue（未知 harness 拒绝，退出码非 0）、list、revoke 三命令 +
     README auth 段（含 enforce 前置=前端接线的披露）
  8. 独立实例 enforce 实测：真 token 放行、冒领 403、事件落库（留档）；
     `PYTHONUTF8=1 ./scripts.sh check` 全绿；DOC-AUTH-001 升 🟡（ACL 缺口
     写明）
test_id：DOC-AUTH-001（既有行由 ⚠️ 升 🟡：API 层矩阵有断言，OS ACL 部分明确
         未覆盖；无新 ID）
测试命令：PYTHONUTF8=1 ./scripts.sh check
当前状态：done（作者完成，待合入；外部验收待认）
完成时间：2026-10-08 16:12
commit：`521f7e9`（实现提交；登记 `4f39574`；合并提交见 git log main）
验证结果：
  docstate    : 90 项检查，0 项失败（含 §38.10 与 §26 的既有断言）
  discipline  : 44 项检查，0 项失败
  ruff        : All checks passed（130 文件格式过）
  pytest      : 491 passed（本任务净增 18：test_auth_tokens 6 + test_auth_matrix 12），exit 0
  测试矩阵     : 令牌生命周期（明文只回一次/库内仅 sha256/吊销即失效/未知与吊销
                的 reason 可区分/未注册 harness 拒发/CLI 三命令与退出码）；
                矩阵逐格（缺/坏/吊销→401、harness↔human 两面互斥 403、
                身份绑定三态〔声明主体/任务 owner/分支段〕、拒绝恰一条
                permission_denied 且零副作用、主体先于事实〔无令牌+未知计划
                →401 而非 404；合法主体+未知计划→404〕、off 模式零行为变化且
                不写审计、policy 键 resource:action 形状）。
  实机 enforce : 独立实例 8894（AP_AUTH_MODE=enforce，独立数据目录，协调者
                8848 未接触）。逐格：无令牌 401 / 坏令牌 401 / 人类令牌打
                adapter 403 / 人类令牌管理链 200×4 / h-B 冒充 h-A 403（任务
                未动）/ h-A 诚实 claim=claimed / h-B 对他人任务 heartbeat 403 /
                本人 heartbeat 200 / harness 打 human 面 403 / decided_by
                不符 403。审计 7 条拒绝=7 条事件（actor/permission/reason/path
                齐全，序号与合法事件交错可追溯）；任务状态仅由合法 claim
                改变、receipts=0。留档 `backend/data/auth-drill/evidence.md`。
已知问题：
  - **enforce 推广前置=前端 token 接线**（本卡未动 frontend）：控制台目前不带
    human token，enforce 下管理/决策操作会 403；在接线完成前生产保持 off
    （README 已用警示块写明）。
  - **OS ACL 三账户**（报告切片 C）未做：ADR-DEV-002 单账户降级不变，
    DOC-AUTH-001 留在 🟡 并把该缺口写在"由谁检查"列。
  - 矩阵中无 HTTP 面的行（pin/unpin、delete_worktree、merge/integration
    write）与 read_secret 无面：不发明端点，继续按报告缺口④口径声明；
    进程面（sweeper/runtime/mergeguard/alerts）不经 HTTP token——按报告
    §4.5 建议=调度中心自身主体，本卡不拦（如实记录）。
  - 读族（projects/groups/tasks/events/diff）与群聊发言保持开放：§17 矩阵
    不覆盖读族，加读层认证会连带前端，属后续卡。
  - **次序分歧记录**：报告建议"先事实后主体"，本卡取"主体先于事实"并把它
    钉死（不外泄资源存在性）——对已认证且合法的请求两者完全等价，卡内声明。
  - "自己的 review lease / 自己的 attempt"仍由服务层事实校验承担（fencing/
    lease owner），policy 表只到角色层；两层都不放宽。
  - 验证管线坑（非应用）：Git Bash `curl -d` 带中文按 GBK 发出→400 body
    解析错误；演练改 ASCII 载荷，应用侧 UTF-8 无问题（见 evidence.md）。
~~~
