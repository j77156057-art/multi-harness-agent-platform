### adapter-hardening：F3/F4/F9——codex 沙箱、opencode 启动、replay worktree 路径（✅ done）

~~~text
任务编号：adapter-hardening
负责人 Harness：Qoder
开始时间：2026-10-09 00:12
依赖任务：e2e-harness-drill（✅ f54420c；F3/F4/F9 的证据来源与配方）；
          MVP-10b（✅；MVP-ADAPTER-002 的适配器契约是修改对象）
工作区：.worktrees/qoder/
真相源引用：drill 报告 F3（codex Windows workspace-write 沙箱
            `CreateProcessAsUserW failed: 2`；演练以 `--sandbox
            danger-full-access` 走通）、F4（opencode 由 python 直启不稳——
            后台服务 DB 报错/usage dump；`bash -lc 'exec "$0" "$@"'` 壳稳定，
            演练配方）、F9（claim replay 响应不含 worktree 路径——重启后的
            harness 找回不了工作树）；R10 §25 phase 5（真实 adapter 契约）；
            R10 §13（沙箱/ACL 边界：平台侧 v1 边界=loopback+Host+token，
            沙箱收回多账户阶段）
范围声明：
  - **本切片（三条修复，全部收进适配器本体，演练配方转正）**：
    ①**F3 codex 沙箱**：`CodexAdapter.command()` 在 nt 上默认
      `codex exec --json --sandbox danger-full-access <order>`（演练已验证的
      组合）；posix 不变（`--full-auto`）。新增 `AP_CODEX_SANDBOX` ∈
      {auto(默认), workspace-write, danger-full-access}（未知值拒绝启动）；
      模块 docstring 与 README 把**安全边界说清**：Windows 上 codex 自带
      沙箱起不了 shell，适配器改用全访问模式——平台边界仍是 loopback+Host
      +令牌，沙箱性回收在 ADR-DEV-002 多账户阶段。
    ②**F4 opencode 启动**：`OpenCodeAdapter.command()` 在 nt 且 `bash` 可用时
      经壳启动（`bash -lc 'exec "$0" "$@"' opencode run …`，演练配方）；
      否则直启。新增 `AP_OPENCODE_LAUNCH` ∈ {auto(默认), bash, direct}。
      两项都加可注入 `platform` 参数（默认 os.name）供测试钉两平台路径。
    ③**F9 replay 路径**：`ClaimItem` 增 `worktree_path`（replay 从 workspaces
      表取；fresh claim=NULL——此时工作树还没建）；`CliHarnessWorker.work_on`
      在重放且未给 `on_claimed` 时接住该路径——重启进程不再旁路查表。
  - **显式出范围**（记录不静默）：codex 模型生成的带引号命令被 cmd 拆
    （`pathspec 'sub''`）属 CLI 内部行为，不修；opencode 单次 provider 错误
    即退（无重试，codex 有 5 次）不假装有重试——两者记载仍属实；F10
    （codex usage 未接预算）留给预算/成本卡；F8（ConnectError 映射）不在本卡；
    不改 §7 接口、不加动词、不动状态机与表结构（零迁移）。
  - **零迁移**；README 增「Harness CLI 环境要求」小节（codex 不在 PATH 的
    前置、两开关、沙箱披露）。
允许修改目录：backend/app/adapter/**、backend/app/config.py、backend/tests/**、
            README.md（环境要求小节）、TASKS.md、docs/traceability.md、
            docs/tasks/notes/adapter-hardening.md、
            docs/claims/qoder--adapter-hardening.md
禁止修改目录：backend/app/state/**、backend/app/verify/**、backend/app/merge/**、
            backend/migrations/**、frontend/**、docs/spec/**、tools/**
交付分支：agent/local/qoder/adapter-hardening/a1
完成标准：
  1. 两适配器的 nt/posix argv 逐字断言（注入 platform）：codex nt=沙箱全访问、
     posix=--full-auto；opencode nt=bash 壳（$0/$@ 形态）、posix 直启；
     `AP_CODEX_SANDBOX=workspace-write` 时 nt 也用 workspace-write（显式覆盖）；
     未知值启动即拒绝（fail-closed）；bash 缺失时 opencode 回落直启（可测）
  2. F9：claim（fresh）→`worktree_path` 为空；start 后 replay（同
     claim_request_id）→ `worktree_path` 等于 WorktreeManager 分配的路径；
     worker 重放（无 on_claimed）用响应路径继续跑（scripted runner 证）
  3. 回归：既有适配器套件全绿（argv 契约原断言按新默认更新；
     `test_real_adapters` 的 codex 契约断言同步）
  4. 真实 CLI 抽查（最小用量，披露配额）：codex 走修复后的 argv 真跑一次
     小任务成功、opencode 经壳真跑一次成功（或如失败给出原始输出与判定），
     留档到卡。
  5. `PYTHONUTF8=1 ./scripts.sh check` 全绿；traceability 新增
     `MVP-ADAPTER-003`
test_id：MVP-ADAPTER-003（新登记）
测试命令：PYTHONUTF8=1 ./scripts.sh check
当前状态：done（作者完成，待合入；外部验收待认）
完成时间：2026-10-09 00:40
commit：`e68c48f`（实现提交；登记 `2b6e99d`；合并提交见 git log main）
验证结果：
  docstate    : 90 项检查，0 项失败
  discipline  : 44 项检查，0 项失败
  ruff        : All checks passed
  pytest      : 498 passed（本任务净增 5：codex 两平台 argv+覆盖、opencode
                壳/直启/bash 缺失回落与开关覆盖、两开关 fail-closed、F9 replay
                round-trip、F9 worker 无旁路续跑），exit 0
  真实 CLI 抽查 : 走**修复后的 adapter.command() argv** 真跑（披露：两次最小
                prompt 消耗用户中继配额；留档 backend/data/adapter-smoke/）：
                - codex nt argve= `exec --json --sandbox danger-full-access`：
                  进程启动并到达模型请求段——**F3 的
                  `CreateProcessAsUserW failed: 2` 不再出现**；
                - opencode nt argv= 壳（`bash -lc 'exec "$0" "$@"'`）+ `run
                  --format json --auto`：经壳建出 session 并发 step_start——
                  **F4 的直启 DB 报错形态不再出现**；
                - 两者均止步 `returncode=1`：**127.0.0.1:8402 中继当时未监听**
                  （外部依赖，curl 000/netstat 空）——启动层修复各自证实，
                  模型往返未完成如实记录；中继恢复后可用
                  backend/data/e2e-drill/ 脚本复跑全链。
  测试要点     : 两平台分支用可注入 platform 钉死（不依赖宿主平台）；开关值
                非法 → Settings() 抛错（fail-closed）；`bash_path=""` 语义
                = 机器上没有 bash；F9 的 fresh=null / replay=活工作树路径 /
                同 attempt 非新 attempt；worker 重放无 on_claimed 直接续跑。
已知问题：
  - **模型往返未完成**（above）：中继是外部依赖，启动层与契约层已证；
    卡不冒充全绿。
  - codex 带引号命令经 cmd 被拆（F3 的 `pathspec 'sub''`）与 opencode 单
    provider 错误即退——CLI 内部行为，记载仍属实，不修不复刻；
  - F10（codex usage 未接预算）与 F8（ConnectError 映射）不在本卡；
  - 顺带修掉一个**已引爆的时间炸弹**（非本卡引入）：
    test_sweeper_deps_resources 的持有者资源租约用冻结 NOW 播种，而 claim
    路径读真实时钟——越过 2026-10-08 21:00 本地即 `queued` 断言翻红；续授
    时钟同理（冻结时钟授予的租约在真实时钟下出生即死、replay 拒绝重发
    token）。改为真实时钟播种/续授后修复；与 frontend-token 卡修的
    test_sweeper_workers 同族（导入时刻 vs 真实时钟 vs 冻结日期三种形态）。
~~~
