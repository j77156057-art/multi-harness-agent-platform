### rules-tighten：F5/F7/F11——cross_provider、worktree 回收、差分收紧（✅ done）

~~~text
任务编号：rules-tighten
负责人 Harness：Qoder
开始时间：2026-10-09 01:05
依赖任务：e2e-harness-drill（✅ f54420c；F5/F7/F11 证据）；runtime-driver
          （✅ 23d4dac；verify 侧修改的落点）；permission-matrix（✅ a968ac7；
          provider 面的话语是矩阵的输入之一）
工作区：.worktrees/qoder/
真相源引用：R10 §21/§10（`review_policy=cross_provider` 字段语义——F5 现状
            "只存字段"）；§14 L795（"merged、abandoned、cancelled 后进入
            pending_archive"；"changes_requested 返工必须先冻结旧 attempt，
            旧 worktree 进入 pending_archive"；"归档包含 git bundle、patch、
            logs 和 receipt"；"归档校验通过后才回收 worktree"）；§10 补充说明
            L513/L517（verify_code_failed 与 awaiting_review 侧失败边"释放
            Lease、冻结 attempt、归档旧 worktree"）；§16 四步差分（L901-907）
            与 L542/L942（failure_reason 冻结四值）＋ L432（verify_code_failed
            含"differential 不成立"）
范围声明：
  - **本切片（三条收紧，全部落在既有语义内，不新增事件名/不新增值域）**：
    ①**F5 cross_provider 强制**：`review_claim`/`submit_review` 在
      `review_policy == 'cross_provider'`（v1 唯一产出的取值）时解析
      owner 与 reviewer 在 HarnessRegistry 的 provider：同 provider →
      409 `review.cross_provider_required`；任一侧未注册 → 409
      `review.provider_unknown`（fail-closed：自报不是授权——与 probe 同口径）；
      `review_policy` 出现 v1 未定义取值 → 409 `review.policy_unknown`
      （不发明第二套策略，新取值须走卡）。自审禁令保留（先于 provider 检查）。
    ②**F7 worktree 回收**：新巡检 `worktree_recycle`（第 15 员，插在 budget
      之后、两个提醒巡检之前——"提醒最后跑"的约定不破）。候选（§14/§10 原文）：
      active workspace 且（task 终态 merged/cancelled/abandoned/changes_requested
      OR attempt 已被新 attempt 顶替）。逐条走既有
      `archive_and_recycle`（bundle→verify→删除；失败留树给人并记 skipped，
      不拖垮巡检）。capacity 随释放自然回落（drill F7 的容量耗尽问题闭环）。
    ③**F11 差分收紧**：VerifyWorker 在 differential=required 且有 base 时，
      若与基线**无任何文件差异**（空产出/空提交）→ 不再放行：
      receipt 记 `failure_reason=code_failed`（冻结四值不动）+ 新字段
      `empty_diff: true`（证据可归因），走 verify_code_failed →
      changes_requested（L432 的"differential 不成立"）；human 逃生口仍在
      （differential:not_applicable → awaiting_human，§16 步 5）。
  - **显式出范围**（记录不静默）：cross_provider 只查 provider 不同，不查
    "更细的可信级别"（registry 没有该话语）；worktree_recycle 不做
    "归档保留策略/磁盘配额"（archives 目录清理属运维）；F11 不修改验收语法
    （验收含 delta 语义的"判据设计"仍是计划侧的人类职责——平台只堵
    "全空产出"，不替靶场设计判据）；F8/F10 仍不在本组。
  - **零迁移**（全部现有表；回收用既有 archive 目录与 workspaces.status）。
允许修改目录：backend/app/tasks/service.py（review 守卫）、
            backend/app/verify/worker.py（差分收紧）、
            backend/app/scheduler/**（新巡检+顺序）、backend/tests/**、
            docs/tasks/notes/rules-tighten.md、
            docs/claims/qoder--rules-tighten.md、TASKS.md、docs/traceability.md
禁止修改目录：backend/migrations/**、backend/app/state/**、docs/spec/**、
            tools/**、frontend/**、backend/app/merge/**（merge 侧不动）
交付分支：agent/local/qoder/rules-tighten/a1
完成标准：
  1. F5 反例逐条：同 provider 的他人 harness 领取 → 409
     cross_provider_required；未注册 reviewer/owner → 409 provider_unknown；
     自审仍 409 self_review_forbidden（次序：自审先判）；异 provider 正常
     领取+提交+approve（正例照旧全链）
  2. F5 对既有事实校验零破坏：全量 pytest 绿（第 3 步的 fixture 补注册）
  3. F7：终态/顶替/changes_requested 三种候选各一条回收（bundle+patch 落
     archives、workspaces→archived、树删除）；活跃 attempt 绝不回收；
     双跑幂等；archive 失败（注入 WorktreeError）→ skipped 且树在、巡检不崩；
     容量场景：占满 → start 拒 → 回收后 start 成功
  4. F11：空提交（head 树==base 树）→ receipt 落库含 `empty_diff: true`、
     failure_reason=code_failed、任务 changes_requested、事件
     verify_code_failed；有差异的旧行为逐字不变（既有 verify 用例绿）；
     not_applicable 模式不受本收紧影响（人行还在）
  5. 巡检顺序测试更新为 15 员、新位置（budget 后、提醒前）钉死；
     `PYTHONUTF8=1 ./scripts.sh check` 全绿；traceability 新增
     `CORE-RULES-001`
test_id：CORE-RULES-001（新登记）
测试命令：PYTHONUTF8=1 ./scripts.sh check
当前状态：done（作者完成，待合入；外部验收待认）
完成时间：2026-10-09 01:35
commit：`e2a4545`（实现提交；登记 `f634f71`；合并提交见 git log main）
验证结果：
  docstate    : 90 项检查，0 项失败
  discipline  : 44 项检查，0 项失败
  ruff        : All checks passed
  pytest      : 511 passed（本任务净增 13：test_rules_tighten.py + 既有
                fixture 补注册），exit 0
  F5 矩阵      : 同 provider 领取 409 cross_provider_required；未注册
                reviewer/owner 409 provider_unknown（claim 与 submit 两处）；
                自审仍 409 self_review_forbidden（次序在先）；未定义策略值
                409 review.policy_unknown；异 provider 正例 approve 全链
                照旧（merge_support/test_adapter/test_real_adapters/
                test_rework_loop 的 fixture 全部补注册、provider 互异）。
  F7 矩阵      : 终态（merged，真 MergeWorker 合并后）→ recycled + bundle/
                patch 落 archives + 树删；活跃 attempt 不回收（F9 重启窗口）
                → 顶替后回收；changes_requested 冻结回收（§14 L795/§10
                L513）；archive 失败（注入 WorktreeError）→ skipped + 树在 +
                巡检不崩；容量 1 时 start 拒 → 回收后 provision 成功（drill
                F7 的容量闭环）；runner 顺序测试 14→15（budget 后、提醒前）。
  F11 矩阵     : 空提交（树==base）→ failed + code_failed + receipt
                `empty_diff:true` + changes_requested；有真实差异的旧通过行为
                逐字不变（receipt `empty_diff:false`）；not_applicable 模式
                不受收紧影响（人行逃生口仍在，§16 步 5）。
已知问题：
  - F5 只比较 provider 串（注册表没有更细的可信级别话语）；同 provider 的
    不同模型（如两个 codex 实例）间审查也被拦——这是"跨 provider"的字面
    语义，更细的策略留给未来卡；
  - worktree_recycle 不做归档保留策略/磁盘配额（archives 目录清理属运维）；
    被顶替判定读 tasks.current_attempt_id，与 §14 的 pending_archive 字面
    状态没建列——登记为后续小卡（"pending_archive" 目前只是语义态）；
  - F11 只堵"全空产出"，不替靶场设计验收判据（"判据要含 delta 语义"仍是
    计划侧人类职责，drill F11 的教训原文在报告里）；
  - F8/F10 仍不在本组（ConnectError 映射 / codex usage 接预算）。
  - 排障留痕：首轮门禁 3 红——F5 的 fixture 漏网（grep 只扫下划线命名，
    HTTP 路径 `/review/submit` 与 CLI `review-claim` 的审查流没扫到）。
    `test_api_flow`×2、`test_cli_flow`×1 补注册后全绿（`0d4858c`）。
    教训：跨面规则的落点扫描要把 **HTTP 路径与 CLI 命令字符串**一并列出
    （本卡 §"完成标准"的 F5 反例当初只写了服务层命名）。
~~~
