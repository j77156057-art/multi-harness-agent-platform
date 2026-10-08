### adapter-hardening：F3/F4/F9——codex 沙箱、opencode 启动、replay worktree 路径（🚧 doing）

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
当前状态：doing
~~~
