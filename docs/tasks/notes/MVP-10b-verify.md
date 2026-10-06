### MVP-10b-verify：MVP-10b 外部验收（✅ done，通过附遗留清单 5 项，无 P1）

~~~text
任务编号：MVP-10b-verify
负责人 Harness：TRAE
开始时间：2026-10-06 05:45
依赖任务：MVP-10b（Qoder，作者完成提交 622b1f2，分支 tip 8d50fb5；
          2026-10-06 04:23 经 ae07649 以 --no-ff 合入 main，先合后验）
工作区：.worktrees/trae/
真相源引用：R10 §7（Adapter 16 个动词；未实现动词显式 UnsupportedVerb）、
            §6（注册四元组 harness_id/provider/adapter_version/
            registration_version 可追踪；能力探测）、§13（Lease 与 fencing；
            丢 token 后的恢复：复放 Claim 对活跃未过期 Lease 轮换 token）、
            §14（worktree 与分支命名；危险分支拒绝）、§16（submit 与 receipt）、
            §25 阶段 5（退出条件：probe / Claim / Heartbeat / Submit / Verify /
            Push Proxy / 超时 / 恢复 / 危险操作拒绝，逐 Adapter 参数化）、
            §26（MVP-ADAPTER-002）、docs/spec/mvp-task-checklist.md MVP-10b
允许修改目录：TASKS.md、docs/claims/46-trae--MVP-10b-verify.md（验收者不改被验代码）
禁止修改目录：docs/spec/**、backend/**、tools/**、docs/adr/**、docs/prep/**、
            docs/claims/ 下非本任务的文件
交付分支：agent/local/trae/MVP-10b-verify/a1
背景：MVP-10b 已由 Qoder 标记 done（作者自陈门禁全绿、新增 29 个测试），
      但在外部验收前即被合入 main（ae07649，第五次先合后验，待验收结论
      出来后按惯例在违规记录区留档）。本任务沿用 MVP-04/05/06/08-verify
      形式：不采信卡片自陈，逐条找代码行 + 机械测试断言双证据，决定
      MVP-11 能否以它为基线。
核查计划（逐条双证据）：
  1. 三 Adapter（codex/claude/opencode）注册四元组与能力探测，argv 单点
     构造且有逐字断言；CLI 无头契约来源（--help 实测/文档）是否如实标注；
  2. §7 全部 16 动词路由齐（含 10a 遗留的 list_ready_tasks/read_context
     隐性 404 是否真补），未实现动词显式 UnsupportedVerb，不允许静默 404；
  3. 真实全链路 claim→heartbeat→publish(Push Proxy)→submit→verify→
     review→merge→merged：真实 HTTP + 真实 worktree，模型进程替身边界
     是否如实声明；
  4. §25 阶段 5 退出条件 9 条逐 Adapter 参数化；超时杀进程 abandon、
     kill-on-deadline 对真实子进程有独立验证；
  5. 恢复：复放 Claim 对活跃未过期 Lease 轮换 token 且库中只存哈希、
     attempt_no 不增；已过期/已释放不轮换；
  6. 危险操作拒绝：main 分支/不可达 commit/旧 epoch/无路由动词；
     visibility=redacted 显式 422 是否站得住（§17 未定义语义）；
  7. 越界核查：diff 不得碰禁止目录（db/events/state/plan/lease/verify/
     merge/git、R10、docstate、discipline）；
  8. 独立重跑门禁（PYTHONUTF8=1 与不带各一次，核实作者卡 GBK 已知问题
     是否只影响 mergeguard 且确属基线既有）。
test_id：MVP-ADAPTER-002
测试命令：.venv\Scripts\python.exe tools\docstate\docstate.py；
          .venv\Scripts\python.exe tools\discipline\discipline.py；
          cd backend; ..\.venv\Scripts\python.exe -m pytest -q -p no:warnings
当前状态：✅ done（通过附遗留清单 5 项，无 P1；验收意见见本文件 MVP-10b 卡前）
完成时间：2026-10-06 06:10
commit：ea4126d（验收证据回填；登记提交 9dc09b8）
验证结果：
  结论：✅ 通过，无 P1，遗留 R1-R5 五项（2×P2、1×P3、2×info），不阻塞 MVP-11。
  docstate 90/0；discipline 42/0；tools 27 passed；ruff clean（73 files）；
  backend 全量 224 dots 零失败（PYTHONUTF8=1）；新套件 46/46 rc=0；
  argv 契约对本机 claude 2.1.274 / opencode v2.0.22 真实 --help 逐字复核一致，
  codex 本机未安装与声明一致；作者卡 GBK 问题不复现（utf8_mode=0 下 14/14）。
  流程：第五次先合后验（ae07649 04:23），已在违规记录区留档并提出
  「待外部验收」机器可判枚举建议。
~~~

### ❌ chain-template 优化：已做、已测、已否决（WorkBuddy，05:15-05:35）

**结论：负收益，已撤销。** 记在这里是为了不让后来人重做一遍。

做法：session 级模板 + `shutil.copytree`，把「建库 + 迁移 + canonical repo + seed」
这四步（每个用例都相同）变成复制一份成品骨架。局部看极快：**建骨架 98.57s → 复制 0.09s**，
快约 1000 倍。

**但全量测下来更慢**（三次测量，环境均为 0 个 python 进程的空闲窗口）：

三次全量测量（环境均为 0 个 python 进程的空闲窗口）：

- 基线 run1：479s，17 passed
- 基线 run2：525s，17 passed
- 优化后：**647s**，17 passed

**为什么不更快**：单链确实从 28.2s 降到 16-20s，但 17 个用例全量只省下约 100s，
而全量反而多花 120-170s。差值来自**累积效应** —— 每个副本要在自己的
`canonical.git` 上跑 `git worktree add`，副本越多、git 元数据操作越慢，
这部分抵消了复制带来的收益。单链测得的好处在全量下不成立。

**顺带纠正一个错误定位**：我曾把瓶颈归为「SQLite 关连接时的 WAL checkpoint
30.1s × 3」。那是在 7 进程并发环境下测的。空闲环境重测：`initialize` 只要
**0.32s**、开连接 + 关 0.11s。**旧的 cProfile 数据全部是并发污染的**，
真实的 30s 来自别处（大概率是 worktree / git 操作），尚未定位。

**方法论（今天栽了三次）**：
1. **基线与优化必须同环境、同时间窗测**。我先在 7 进程环境测基线、后在 0 进程
   环境测优化，两个数字根本不可比。
2. **单链快 ≠ 全量快**。单链 16-20s vs 基线 28.2s 是真实收益，但全量 647s vs 479s
   是真实亏损 —— 两个结论都成立，必须都测。
3. **`exit=0` 不等于通过**。中途报过一次「优化后 1 小时 48 分 / exit=0」，实际输出
   只有 2 个点，17 个测试只跑了 2 个就退出，SIGTERM 被吞。判结果要看测试点数。

**改动去向**：WIP 曾被抢救到 `agent/workbuddy/chain-template-rejected`
（`fcfd303`），现已撤销；`backend/tests/chain_template.py` 删除，
`test_verify.py` 的 `build_chain` 回到基线。认领记录见
`docs/claims/45-workbuddy--chain-template-closeout.md`。
