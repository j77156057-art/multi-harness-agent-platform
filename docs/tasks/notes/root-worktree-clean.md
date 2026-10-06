### root-worktree-clean：断言根工作区非合并状态下无未提交改动（doing）

~~~text
任务编号：root-worktree-clean
负责人 Harness：OpenCode
开始时间：2026-10-06 00:35
依赖任务：merge-interlock（⏸️ 待合入；已被 TRAE `merge-interlock-review` 退回，
            本任务已按其问题 1-4 修复完成，见下方「评审退回修复」章节）
工作区：.worktrees/opencode/
真相源引用：AGENTS.md §0与 H-7（仓库根目录只用于合并与跑门禁，不要在那里编辑）、
            dev-convention.md「会被门禁红掉的」清单
            TASKS.md `merge-interlock-review` 任务卡（TRAE 的 6 条问题清单）
允许修改目录：tools/discipline/**、tools/mergeguard/**、backend/tests/**、
            docs/dev-convention.md、docs/traceability.md、TASKS.md
禁止修改目录：backend/app/**、backend/migrations/**、docs/spec/**、tools/docstate/**
交付分支：agent/local/opencode/root-worktree-clean/a1
背景：今天合并反复被挡，根因不是工具而是**有人在合并区里做开发**。AGENTS.md 规定
      根目录「只做合并和在 main 上跑门禁」，但Qoder、TRAE 轮流在里面写代码、
      留未解决冲突。互斥锁管不了这个——它只拦「合并」，不拦「在合并区开发」。
      `mergeguard` 只能拒绝自己那一次合并，无法让占用者��担代价。
完成标准：
  1. `discipline` 新增 rootworktree 组：非合并状态下根工作区必须干净   ✅
  2. 受控合并进行中放行（**判据已按 TRAE 评审问题 1 收紧**：              ✅
     MERGE_HEAD 存在 **且** 无未合并条目 **且** 合并锁持有者存活）
  3. 无法定位根工作区 / git 不可用时 **fail-closed**，与 merge 组一致   ✅
  4. 从任意 worktree 运行都能定位到根工作区（经git common dir 反推）  ✅
  5. 单测覆盖：干净 / 有未提交改动 / 有未跟踪文件 / 合并中 / 无 git   ✅
  6. `docstate` / `discipline` / `ruff` / `pytest` 结果如实记录       ✅
test_id：MVP-PROCESS-002
测试命令：./scripts.sh check（等价分步：docstate + discipline + ruff + pytest）
当前状态：✅ done（代码侧完成；合入 main 需与「问题 6 耦合」一并拍板）
commit：本分支 tip 见 `git log -1`（合并前已补全下方验证证据）
问题 6 [中/操作面] 合入即时效应 —— 按 **C 方案**落地（2026-10-07）
  原问题：本组一合入，根工作区脏时所有人的 `check` 与合并全部被堵。这是
  刻意的强制函数，但**代价落在所有人身上而只有一个人是原因** —— 实测堵了
  十几次合并，其中几次是无关 agent 的。
  **C 方案：把判定对象从「所有人」换成「占用者本人」。**
  - 新增 `Result.warn()` 第三个等级。不判红、不影响退出码，但**默认就打印**
    （不需要 `--verbose`）且计入 `--json` 的 **stdout** 体。
  - `check_root_worktree` 作用域反转：
    · 调用方**就在根工作区**（占用者）→ 严格判红，代价落在他身上；
    · 调用方在**别的 worktree** → 记 WARN，点名文件、说明占用者自己会看到
      红灯，但不判红、不阻塞他干活；
    · **定位不到主工作区 / git 不可用 → 对任何人都 fail-closed 判红。**
  - 立足点：占用者要在根工作区留下改动，就**必须待在根工作区里**跑命令，
    所以他一定拿得到红灯；别人只是看得见。
  - 为什么不用 `chk(True, ...)` 冒充通过：那是**内容为假的绿色项**，绿灯
    里含假账，比不检查更坏。这正是 Claude 在 claim-seq 卡里记的同形教训。
  - 为什么不只做「降级为告警」（TRAE 卡的退路 B）：那样占用者也拿不到
    红灯，断言基本没用。C 与 B 的区别就在「谁承担」。
  实测（同一份占用事实）：占用者退出码 1；无关 agent 退出码 0 并看到 WARN。
  同步更新 `docs/dev-convention.md` 的「会被门禁红掉的」表（含项数 42→45）。
  新增 3 条用例；`Result.warnings` 另有子进程用例钉住「告警不是静默通过」。
  **AGENTS.md 未改** —— 不在本任务允许目录内；按 AGENTS.md 自身的分工，
  H-1/H-8 类细则归 `docs/dev-convention.md`。

合入前提（阻塞中，非代码问题）：
  1. ~~问题 6 需团队拍板~~ → 已定为 C 并落地（见上）。
  2. **合并须走一次例外**：main 上跑的是 mergeguard 首版（无 `--no-commit`），
     用它合本分支会重现「门禁红了但 merge commit 已落地」。
     **「用受控合并合入受控合并的修复」在结构上做不到** —— 需要协调者
     允许一次裸 `git merge` 把工具本身送上线，或先单独修好 main 上的首版。
验证结果：
  - docstate  : 90 项检查，0 项失败
  - discipline: **43 项检查，1 项失败** —— 唯一失败是本任务新增的
    `rootworktree` 组，且是**如实报告当前根工作区正被占用**
    （`M docs/dev-convention.md`、`M scripts/new-ai.ps1`、`?? scripts/lib/`、
    `?? scripts/migrate-venvs.sh`）。这是断言生效的证据，不是缺陷。
  - ruff check : All checks passed!
  - pytest     : 169 passed
  - **豁免语义**：`discipline --skip-group rootworktree` → 42 项 0 失败，
    并打印 `[skip] 豁免组：rootworktree（由调用方声明理由）`。
  - `test_discipline.py` **24 passed**（本任务前 14，新增 10 条）
  - **豁免后的门禁全绿**：`docstate 90/0`、`discipline --skip-group
    rootworktree 42/0`、`ruff clean`、`pytest 169 passed`。
    mergeguard 合并时走的就是这条豁免路径，所以它现在不会被自己的门禁锁死。
  - 新增用例覆盖：干净 / 有改动 / 有未跟踪文件 / 合并中放行 /
    从 linked worktree 运行仍查主工作区 / 无 git 时 fail-closed /
    断言确实落进 rootworktree 组且被 result.failed 收集
  - 过程中我自己写错一次：`test_root_worktree_allows_merge_in_progress`
    把 `MERGE_HEAD` 写在仓库根而非 `.git/`，得到假阴性。已修。
    这也说明断言的判据位置必须写对，否则「放行」会变成「误报」。

评审退回修复（TRAE `merge-interlock-review`，2026-10-06 02:34，commit 611707a）
--------------------------------------------------------------------------

TRAE 对 merge-interlock 分支做了独立评审（逐行读 diff + 临时 worktree 实跑
+ 两个独立子 agent 双路复核，6 条 2/2 确认），结论「方向正确、核心机制本机
实证可用，但问题 1-4 修复并补测试后再合」。本任务已完成 1-4：

问题 1 [高] 门禁红之后是确定性死路
  症状：门禁红 return 5 时留下本工具自己的 MERGE_HEAD，提示「修好门禁后重跑
  本命令」；而入口预检见 MERGE_HEAD 一律 exit 7，还误报「另一个 agent 正在
  跑 git」并诱导删锁，删完又被脏工作区检查挡回 exit 2。`--skip-gate` 路径
  同样被挡。
  根因：预检只有「MERGE_HEAD 存在与否」这一个信号，分不清「自己的」和
  「别人的」。
  修法：合并进入待提交状态时写 `merge-pending.json`，记录 **MERGE_HEAD 内容
  + index 树哈希**。入口据此分流：内容相符 → 跳过合并步骤，直接续跑门禁并
  提交；不符或无记录 → 仍然 exit 7，绝不接管。**fail-closed**。
  附带修掉的死循环：`--skip-gate` 重跑若仍只 return 0 不提交，会永远停在
  待提交态。现改为「续跑 + --skip-gate = 跳过门禁直接提交」，与恢复指引一致。

问题 2 [中] 最终 commit 无重试
  症状：有界退避只包住 merge，不包更新 `refs/heads/main` 的那笔 commit；
  撞锁 exit 6 后叠加问题 1，恢复全靠人肉。
  修法：新增 `_commit_with_retry`，同一退避策略包住 commit。重试**不需要**
  重新 merge 或 abort —— MERGE_HEAD 还在，重跑 commit 就是同一笔提交。

问题 3 [中] 两条核心新行为零测试
  症状：`test_cli_merges_and_releases_lock` 被删且无替代，「门禁全过 → commit
  → HEAD 前进」这条**唯一生产成功路径**零覆盖；重试/退避/abort 27 行零断言。
  根因：临时仓库夹具里没有门禁脚本，门禁那段分支结构上就够不到。
  修法：新增 `_install_gates` 在临时仓库里装**可提交的真门禁替身脚本**，
  并用 `.git/gate-fails.flag` 作失败开关（放在 `.git/` 下而非工作区 ——
  放工作区会被「拒绝脏工作区」守卫挡掉，就构造不出「先红后绿」的续跑场景）。
  新增 17 条用例，覆盖成功路径、门禁红不提交、重跑续跑、续跑拒绝他人暂存
  内容、续跑容忍未暂存/未跟踪、归属不符拒绝接管、陈迹清理、abort 归属
  三分支、merge 与 commit 的重试预算、冲突不重试。

问题 4 [中] 重试前无条件 abort 他人的合并
  症状：`_git("merge", "--abort")` 无条件执行。3/6/9s 退避窗口内若另一 agent
  裸跑发起合并，会被静默 abort —— 正是本文件第 4 次事故记录与本工具自身
  docstring 第 3 条所禁止的形态。预检是入口一次性快照，关不住这个窗口。
  修法：`_abort_only_our_merge` 按 MERGE_HEAD **内容**判定归属：
    - 等于本次 merge 预期的 commit → 是我们的，回退；
    - 不存在 → 本次 merge 连标记都没落盘（实测 git 先写 ORIG_HEAD），无可
      回退，直接重试（不误伤）；
    - 存在但指向别的 commit → 他人的，抛 `_ForeignMerge`，**不 abort 并
      exit 7**。

本轮测试抓出的一个真实漏洞（值得单记）
  续跑守卫的第一版用 `git diff --name-only`（未暂存改动），被新增用例抓出是
  **假严格**：未暂存与未跟踪的内容 `git commit` 本来就不会带进提交，拒了
  只是白拒；反过来真正的危险（别人手敲 `git add` 塞进暂存区）它却完全
  看不见。改为比对 **index 树哈希**与记录指纹 —— 那才是「要提交的内容是否
  恰为本次 merge 产物」的精确判据。同时补一条镜像用例，确认未暂存/未跟踪
  改动**不会**被拒也不会被卷进 commit。
  这条与 Claude 在 claim-seq 卡里记的教训同形：判据选错时，严格只是自我
  安慰，既漏真问题又添假问题。

验证结果（修复后）
  - docstate  : 90 项检查，0 项失败
  - discipline: 42 项检查，2 项失败 —— 均为认领序号（合并 main 后必然撞号，
    见下方「序号」说明），非本轮改动引入
  - ruff check : All checks passed!
  - ruff format: 67 files already formatted
  - pytest tools : 37 passed
  - pytest backend: **212 passed**
  - test_merge_guard.py: **32 passed**（修复前 15，新增 17），连跑 3 次 0 flake
  - **负向测试**：退回修复前的 cli.py 后跑新用例 → **15 failed / 17 passed**，
    即新增用例确实能抓住这 4 个问题，不是空断言
  问题 5 [中] pytest 硬崩 —— 根因已定位（不采用 xfail，见下）
  现象：TRAE 机器上整会话跑 `test_merge_guard.py` 100% 硬崩，1~3 个 dot 后
  「reader 线程/进程树死亡」，`-s` 亦然，单跑 linked-worktree 用例零输出崩。

  **我没有复现崩溃本身**：本机该文件 34 passed，连跑 4 次 0 flake，单跑
  linked-worktree 用例 15/15 通过。所以下面区分「已证实」与「推断」。

  已证实的部分：
  1. **本机有两批早前的 pytest 至今仍挂着**（4 小时 / 1.5 小时），每个都被
     一个真实 git 子进程卡死，且留下完整孤儿进程树：
        python -m pytest -> python -> git push -> sh.exe -> git-receive-pack -> git
     `git push` 自身 cpu=0.2s，卡在等子进程。另一批卡在 `git commit` 上，
     且其 git 子进程 PID 发生过变化（52100 -> 34980），说明在反复重启、
     永不推进。这说明「挂死 + 孤儿进程树」在本机是**已发生的事实**，
     TRAE 看到的「进程树死亡」很可能是同一现象在他的 harness 上的表现
     （他那边是 harness 超时后杀掉进程树，我这边是进程被孤儿化）。
  2. **触发源是 git 自动维护**，GIT_TRACE2 实测（git 2.55）：每一次写操作
     —— 哪怕一条普通 `git commit` —— 都会派生 detached 子进程
        child_start[0] git maintenance run --auto --quiet --detach
     它 (a) 继承父进程 stdout/stderr，于是 capture 管道要等它退出才拿到
     EOF；(b) 去抢 packed-refs / ref 锁。
     对照实验：`maintenance.auto=false` + `gc.auto=0` 时派生次数 **0**，
     恢复默认后是 **1**。因果确立。
  3. 本仓库 local/global/system 三级都**没有**配 `maintenance.auto`，
     即默认开启。

  两个被实验**否掉**的假设（记录下来，免得后人重走）：
  - `os.kill(pid, 0)` 在 Windows 上会杀死目标进程 —— **不成立**。实测
    python 3.13.14 / win32 下它是纯探测，子进程安然无恙。所以
    `guard._pid_alive()` 不是元凶（但它仍然值得在 Windows 上换成
    非 TerminateProcess 的实现，见下）。
  - 子进程继承了 pytest 的阻塞 stdin 管道 —— **不成立**。`-s` 下
    `os.fstat(0)` 显示是控制台（isatty=True），构造「hook 从 stdin 读 +
    父进程不关写端」也**没能**复现挂死。

  推断（未能直接验证，但与上述证据一致）：测试派生上百个 detached
  maintenance 进程，彼此与父进程抢 ref 锁并争抢 capture 管道；调用方
  （人或 agent harness）超时后杀掉整棵进程树 —— 表现为「几个 dot 之后
  进程树死亡、零输出」，且 `-s` 救不了（问题在子进程继承的句柄，
  与 pytest 自己的 capture 无关）。这解释了 TRAE 报告的全部三个现象。

  修法：**掐掉触发源，而不是用 xfail 掩盖症状**。
  - `repo` fixture 里对每个临时仓库设 `gc.auto=0` + `maintenance.auto=false`。
    测试不需要 gc。TRAE 建议的「会话隔离 / 本机 xfail」被否：xfail 会把
    崩溃记成「预期失败」，绿灯里就掺了假账 —— 而这个工具的全部价值就是
    「绿灯可信」。
  - 所有 git 子进程调用加 `stdin=DEVNULL`（git 派生的 helper 会继承 stdin）
    与 `timeout`（把「永久挂死」变成「一条带上下文的失败」）。
  - `cli.py` 同样加上；`_run_gate` **故意不加** timeout —— 门禁本就是慢
    活儿（pytest 几分钟），给它上限会把好合并误判成门禁红；而门禁挂死的
    代价由 git 层那 300 秒上限兜底。

  验证：merge_guard 34 passed，连跑 4 次 0 flake；pytest backend
  214 passed；pytest tools 37 passed；ruff clean；docstate 90/0。
  本机残留 git 进程数从 4 降到 2（剩下 2 个是那两批早就挂死的旧孤儿，
  与本次改动无关；新跑未产生新的孤儿）。

  **仍需 TRAE 复跑确认**：根因推断链完整，但触发条件依赖他的机器环境
  （git 版本、是否开了 maintenance.auto、harness 怎么杀进程）。若他那边
  仍崩，需要他给出崩时的 git.exe 残留进程与 `git config --get
  maintenance.auto` 的输出，才能把推断变成证实。

问题 6 [低] 预检信号不全（已单独修复）
  症状：预检只看 `index.lock` 与 `ORIG_HEAD.lock`，漏了裸跑 commit / fetch /
  checkout 会留下、而 index 那时已经放开的 ref 侧锁。漏放的后果不是「晚一点
  才失败」，而是要等到真正提交那步才撞 `cannot lock ref 'HEAD'` —— 那时锁
  已取到、合并已完成、门禁已跑完，前置工作全白费。
  修法：锁清单提为模块级常量 `FOREIGN_GIT_LOCKS`，补三个：
  `refs/heads/main.lock`、`packed-refs.lock`、`HEAD.lock`。
  新增 2 条用例：清单必须覆盖这三个 ref 侧锁；每个声明的路径都必须是 git
  真的会写、且真的会挡住某个写操作的位置。

  **布局是实测确认的，不是照抄文档**：造同名锁文件后让真 git 去操作，
  看报错里的绝对路径 —— `index` / `HEAD` 是 per-worktree（linked worktree
  的在 `<common>/worktrees/<name>/` 下），`refs/` 与 `packed-refs` 跨
  worktree 共享。本工具只在根工作区跑，两种布局路径重合，故统一按 common
  dir 拼接。若路径拼错（如写成 `refs/main.lock`），git 根本不碰它，预检就会
  放行一个不存在的障碍 —— 比漏检更坏，所以这条路径正确性有专门断言。

  **一个断言被实测纠正的记录**：我第一版断言「这三个锁必须挡住 `git commit`」，
  实测 git 2.55 下 `packed-refs.lock` 对 commit 只打印 error 而退出码仍是 0，
  它真正挡的是 `pack-refs`。断言按「git 因这个文件拒绝了某个操作」重写。
  若照原样断言，一条**正确的**声明会被判成缺陷 —— 断言比实现更容易写错。
  同理 git 报错里是**绝对路径且用正斜杠**（Windows 上亦然），按 `os.sep`
  转换会匹配失败。

  - 负向测试：从 `FOREIGN_GIT_LOCKS` 摘掉三个锁 → 覆盖断言转红；
    端到端用例改为遍历该常量而非手写名字，将来加锁漏测会立刻暴露。
  - 验证：`test_merge_guard.py` 32 → **34 passed**；pytest backend
    212 → **214 passed**；ruff clean；docstate 90/0。

序号说明：本分支认领序号 40 已被 main 上 Qoder 的 `40-qoder--MVP-10a.md`
占用，合并时需改号。合并顺序必须为 **先 claim-denum（42）后本分支**，否则
main 会出现缺号变红。

已知问题：
  - **这条断言会让所有人的 `./scripts.sh check` 在别人占用根工作区时变红**，
    包括正在自己 worktree 干净干活的人。这是刻意的强制函数——占用合并区不该
    由别人替它付代价——但它确实把个人行为变成了全局门禁。若团队不接受这个
    耦合，退路是降级为告警（只打印不失败），或改成只在根工作区自身运行时才断言。
  - 与 `mergeguard` 的分工：mergeguard 是**事前阻止**（合并时不许有脏改动、
    不许并发）；本断言是**持续施压**（占用合并区会让所有人的门禁变红）。
    两者都需要，缺一不可。
  - 本任务落地时根工作区正被 TRAE 占用（`MM TASKS.md` +
    `docs/claims/36-trae--MVP-05-verify.md`），因此新断言**预期为红**。
    这是断言正在生效的证据，不是缺陷；等对方提交或撤出后自然转绿。
  - CI 里`git status` 恒为干净（全新检出），所以这条断言只在本地有意义。
    不要把它当成CI 门禁来依赖。
~~~
