### MVP-05：Git worktree 与发布代理（✅ done）

~~~text
任务编号：MVP-05
负责人 Harness：Qoder
开始时间：2026-10-05 21:55
依赖任务：MVP-03（代码已合入 main `db9bc19`）、MVP-04（✅ 已验收）
          ★ 依赖判据是「已合入 main 的代码」，与 MVP-04/MVP-08 一致。
工作区：.worktrees/qoder/
真相源引用：R10 §14（worktree、路径和资源；分支命名与生命周期）、§13（canonical Git 拓扑、
            Push Proxy、fencing 的执行点）、§12（attempt 与命令幂等键中的 publish 一条）、
            ADR-058/ADR-059（宿主与发布模式、发布对象传输与账户边界）、§17（权限矩阵）、
            §15（side_effect_intents）、§26（GIT-ACL-001/002、GIT-PACK-001）
允许修改目录：backend/app/git/**、backend/app/api/**、backend/app/tasks/**、
            backend/tests/**、docs/adr/**、docs/traceability.md
禁止修改目录：docs/spec/0.3-R10.md、tools/docstate/**、tools/discipline/**、
            backend/app/db/**、backend/app/events/**、backend/app/state/**、
            backend/app/plan/**、backend/app/claim/**、backend/app/lease/**、
            docs/claims/ 下非本任务的文件
交付分支：agent/local/qoder/MVP-05/a1
完成标准：
  1. 每个 attempt 一个独立 worktree；两个任务不共享可写目录（有断言）
  2. 分支命名 `agent/<group-short>/<harness>/<task-id>/<attempt-id>` 只有一处实现，
     §14 的分支白名单与 Push Proxy 的校验调用同一个函数（R10-09 的三处口径一致）
  3. publish_task_branch 在发布前校验 task / attempt / commit / fencing epoch / 分支白名单；
     旧 epoch 发布失败
  4. 直接写集成分支被拒（canonical 侧的 pre-receive 拒绝，不是靠调用方自觉）
  5. worktree 回收前必须归档（git bundle + patch），归档校验通过才删目录
  6. 触发 `claimed → in_progress` 的 start 现在会 provision worktree，
     即 MVP-08 卡里标注为「脚手架」的那一步有了真实实现
test_id：GIT-ACL-001、GIT-ACL-002、GIT-PACK-001
测试命令：./scripts.sh check
当前状态：done（作者完成并已合入 main；外部验收待认，参照 MVP-04-verify 形式）
完成时间：2026-10-05 22:20
commit：7e94166
验证结果：
  docstate    : 90 项检查，0 项失败
  discipline  : 32 项检查，0 项失败
  ruff        : All checks passed，49 files already formatted
  pytest      : 132 passed（MVP-05 新增 19）
  真 git 断言  : 直接 push 集成分支被 canonical 钩子拒绝（真跑 git push，
                断言 stderr 含拒绝原因且 ref 未移动）；任何客户端 push 均被拒；
                旧 epoch 发布被拒且不落 intent；alternates 越界被拒；
                归档 bundle 校验通过后才回收，目录确实消失（Windows 只读 object 处理过）
已知问题：
  - **`groups` 表没有 `group_short` 列**，而 R10 §14 的分支名第一段就是 group-short。
    本任务把它作为参数传入并在分支名里持久化，`start_task` 缺省回落到 group_id。
    要让分支命名可完全从库推导，需要一次迁移加列（属 MVP-02 的 backend/migrations/**）。
  - **R10 §7 没有面向 Harness 的 `start` 动词**。§10 的 `claimed → in_progress`
    由调度器驱动（guard 是"worktree ready"），所以本任务把它实现为
    `SchedulerService.start_task`：先 provision worktree，再迁移。HTTP 面上**不新增**
    `/adapter/start` —— 新增 §7 未定义的动词会破坏 MVP-08 立下的"路径逐字对齐 §7"。
    代价：MVP-08 卡里"只通过 API"的链路仍有一步不经 HTTP（现由 MVP-10a 的 Fake Harness
    在进程内驱动）。
  - **发布没有可写的事件名**。§13 提到"intent 完成事件和审计记录"，但 §37.7 的 88 项
    里没有 publish 类事件（与 MVP-08 prep 的 G-2 同类缺口）。当前用
    `side_effect_intents(type=push)` + `command_idempotency` 记录，未自造事件名。
  - Windows 上 git 把 object 文件标为只读，`shutil.rmtree(ignore_errors=True)` 会**静默**
    删除失败 —— 回收看起来成功、目录还在。已修为先清只读位再删；这条值得其他任务注意。
  - MVP-08 的 API 测试需注入临时 worktree 管理器：`start_task` 现在真的建目录，
    缺省会落到仓库根的 `wt/` 与 `canonical.git`（测试不得碰）。
  - MVP 简化（清单要求写明）：R10 §13/§17 的三账户 ACL（agent-scheduler /
    agent-harness-<id> / agent-verify）在 MVP 阶段降级为单账户 +
    `--insecure-single-account`。这是**临时措施**，阶段 2 前必须补齐；清单落点写在
    docs/adr/ADR-DEV-002-single-account-acl.md，含阶段 2 前的三账户补齐清单。
  - GIT-PACK-001（Defender 开启时的跨账户 pack/bundle 传输与 intent 对账）在单账户
    模式下无法真实复现跨账户行为，只覆盖传输与对账路径本身，跨账户部分留待阶段 2。

验收意见（TRAE，2026-10-06 00:05，基线 719fca6；不采信卡片自陈，逐条找代码行+机械断言）：
  总评：✅ 通过（附遗留清单；无指向 MVP-05 交付物的 P1）。两条 test_id 的每个
  不变量均取得「代码行 + 真 git 机械断言」双证据；19 个证据用例在基线工作区
  实跑 19/19 退出 0。作者卡 6 条已知问题逐条核实属实，无隐瞒。

  GIT-ACL-001（两个任务不共享可写 worktree）✅：
  - 分支命名唯一实现：backend/app/git/branch.py 是全 app 唯一构造 "agent/"
    前缀处（grep 穷举）；expected_branch / is_integration_ref 同时被
    repo.pre_receive_hook（服务端拒绝）与 publish（发布白名单）调用，
    R10-09「白名单与 Proxy 同一函数」三处口径一致；
  - worktree.provision 按 attempt 幂等复用；assert_isolated 对活动 path
    分组 COUNT>1 即抛 WorktreeIsolationError；短 ID 取 sha256 4 位前缀、
    碰撞递增到 12 位；容量默认 settings.concurrent_worktree=8；
  - alternates 只写 canonical objects_dir，agent gitdir 位于 canonical 之外；
  - archive_and_recycle 先 git bundle create + bundle verify（失败抛错并
    保留目录），patch=base..head，artifacts 落 git_bundle/patch 两条后才
    _force_rmtree（先清 Windows 只读位）；
  - 机械断言：tests/test_git_worktree.py 8 例真跑通过。

  GIT-ACL-002（直接写集成分支失败；旧 epoch 发布失败）✅：
  - repo.pre_receive_hook 拒绝一切客户端 push（integration/main 给专属
    消息，其余 "client pushes are not accepted"），测试真跑 git push；
    scheduler 侧 import_objects（fetch）+ update_ref 不经接收端口；
  - publish 校验顺序实测：integration 目标 → 幂等重放 → epoch（guarded_write）
    → workspace 存在 → 分支必须等于库存分支（403 publish.branch_not_allowed）
    → alternates 必须等于 canonical objects_dir（403）→ commit 在 agent
    gitdir 可达；旧 epoch 拒绝且不写 intent、不动 ref
    （test_old_fencing_epoch_is_rejected）；
  - 成功路径：side_effect_intents(type=push) pending→done、回写
    task_attempts.head_commit、command_idempotency 存 first_result_json，
    重放返回首发结果；
  - 机械断言：tests/test_publish.py 11 例真跑通过。

  ADR-DEV-002 单账户降级 ✅：AP_INSECURE_SINGLE_ACCOUNT 默认 True
  （backend/app/config.py），三账户 ACL 与 GIT-PACK-001 跨账户部分明示
  留阶段 2，与 checklist「单账户降级要求写明」一致。MVP-05 未新增迁移：
  workspaces/artifacts/side_effect_intents/command_idempotency/
  task_attempts 五张表均在 migrations/001_initial.sql，合理。

  门禁实测（2026-10-06 00:05，719fca6 worktree；共享 .venv 原解释器被
  WorkBuddy 原地升级 3.13.12→3.13.14 后 subprocess 崩溃，已用系统
  Python 3.13.3 按 backend/requirements(-dev).txt 锁定版本重建，旧目录
  改名 .venv.bak 留存，各 worktree 的 .venv junction 未动；依赖版本未变）：
  - docstate 90/0；discipline 39/0；pytest tools 14 passed；
  - backend：证据套件 test_publish.py + test_git_worktree.py 19/19
    退出 0（真 git，约 50s）；除 tests/test_merge_guard.py 外全量
    135 项退出 0；ruff check All checks passed、ruff format
    50 files already formatted。

  遗留/观察（不阻塞本验收）：
  1. P2 契约漂移：openapi PublishRequest 的 host_mode / agent_gitdir_uri /
     patch_uri / patch_hash 与 PublishResponse.required 的 created_seq 在
     app/api/schemas.py 无对应字段，patch_apply 模式完全未实现。checklist
     MVP-05 交付清单未列 patch_apply、ADR-DEV-002 明示跨账户留阶段 2，
     故不判违约；但 MVP-06 及 Fake Harness 对接前须裁定「裁剪 openapi 或
     补实现」，否则契约先行字段持续说谎（与 MVP-08 refs 旧案同类）。
  2. P3 错误码一码两义：publish.commit_unreachable 同时承担 404
     （workspace 不存在）与默认 409（commit 不可达）两种语义，建议拆码。
  3. P3 观察：checklist「可提前做」提到的 Windows Git longpaths 预案无
     对应代码（grep 无 longpaths），实测深路径未触发，登记备忘。
  4. 跨任务确认：MVP-08 遗留第 4 项未关闭——test_api_flow.py 的
     start_task 仍由服务层驱动（相对旧版仅新增临时 worktree manager
     注入），MVP-API-001 维持 🟡，待真实 HTTP publish 路径接通后再转。
  5. 环境/测试基建事件（非 MVP-05 交付物，另案跟）：随 main 719fca6
     合入的 backend/tests/test_merge_guard.py（OpenCode merge-interlock）
     在本机 pytest 会话中确定性硬崩——第 1 例后第二个 fixture 建子进程时
     reader 线程 access violation（3.13.14 与重建后的 3.13.3 均复现，
     junction/非 junction 路径均同）；单例可通过、裸解释器复刻同序列
     21 次 git 调用稳定；该文件自身注释（L92-94）也记录过 pytest fd
     捕获下 git 随机 0xC000013A。归属 OpenCode，建议其在本机复跑并修
     测试隔离。本验收以 --ignore 取得 135 项全绿 + merge_guard 单例
     通过为旁证，GIT-ACL 判定不受影响。
  6. P3 文档损伤：TASKS.md 任务表存在并行合并导致的行粘连，docstate/
     discipline 均不报，下次表格整理时一并修。

  合并期间补充证据（2026-10-06 00:30，合并树 main 170e56e + 本分支）：
  7. P3 alternates 换行：实测 worktree 写出的 objects/info/alternates 以
     CRLF（0D 0A）结尾（文本模式 \n 被 Windows 翻译），git 本地传输报
     `object directory .../objects? does not exist`。当前非致命——新对象
     写在 agent gitdir 本地库、测试 19/19 仍过；但阶段 2 严格 pack 传输下
     可能致命，建议写文件改用 newline=""（二进制）或写入前去尾 CR。属
     MVP-05 代码（worktree.py），留待后续任务修，验收者不改。
  8. 环境竞态（与第 5 项同类，非代码缺陷）：合并树后端全量首跑时
     test_api_flow.py::test_approve_then_review_approval 曾 1 次失败于
     `/mingw64/bin/git-upload-pack: Permission denied`（Defender 实时扫描/
     句柄争用特征，与 GIT-PACK-001 预案场景吻合）；同用例单跑 3/3、全量
     复跑 152/152 退出 0。
~~~
