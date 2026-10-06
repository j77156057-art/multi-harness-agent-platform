### 违规记录：MVP-10b 第五次「先合 main 后验收」（ae07649）

**事实**：Qoder 的 MVP-10b（作者完成提交 622b1f2，claims/43 状态
✅ done）于 2026-10-06 04:23 由 `ae07649`（Merge branch
'agent/local/qoder/MVP-10b/a1'，--no-ff 两父 c0ee564/8d50fb5）合入
main；TRAE 的外部验收于 05:45 才登记（MVP-10b-verify，claims/46）。
这是继 MVP-03 / MVP-04 / MVP-08 / MVP-06 之后**第五次同模式违规**，
且 10b 是 MVP-11 的硬依赖（关键路径）。

**为什么闸没拦住**：discipline merge 组只机械核对「任务已登记、卡为
done、证据齐全、合并标题默认」——作者把自己的卡标 ✅ done 即满足，
**「done」无法区分「作者自陈完成」与「外部验收通过」**。GATE-MERGE-
CHECK-01 当年预见的残余盲区之外，这是第二个制度缺口：验收状态没有
机器可判的独立枚举。

**已采取的纠正**：TRAE 以 MVP-10b-verify 按 MVP-ADAPTER-002 / R10
§25 阶段 5 退出条件逐条双证据核查（不采信卡片自陈），独立重跑
docstate 90/0、discipline 42/0、tools 27、backend 224 全过、ruff clean，
并对本机真实二进制逐字复核三 Adapter 的 argv 契约；结论 ✅ 通过附
遗留清单（无 P1），已回填本卡末「验收意见」。

**建议（不在本验收任务实施）**：任务表状态增加机器可判的
「待外部验收」枚举（或约定 ✅ done 必须带验收者署名行，由 discipline
正则核对），使 merge 组能在外部验收前 fail-closed；排入 MVP-11 或
后续纪律任务。

### MVP-10b：真实 Harness Adapter（✅ done）

~~~text
任务编号：MVP-10b
负责人 Harness：Qoder
开始时间：2026-10-06 02:26
依赖任务：MVP-10a（✅ 已合入 main 937eac7）、MVP-05/06/07（均已合入 main）
工作区：.worktrees/qoder/
真相源引用：R10 §7（Adapter 接口的 16 个动词；真实 Harness 接入优先级 1 = CLI/无头模式）、
            §6（注册、能力探测、registration_version 可追踪）、§13（Lease 与 fencing；
            Harness 丢 token 后的恢复）、§14（worktree 与分支命名）、§16（submit 与 receipt）、
            §25 阶段 5（Codex → Claude Code → OpenCode；退出条件：每个 Adapter 通过 probe、
            Claim、Heartbeat、Submit、Verify、Push Proxy、超时、恢复和危险操作拒绝测试）、
            §26（MVP-ADAPTER-002）
允许修改目录：backend/app/adapter/**、backend/app/claim/service.py、backend/app/api/**、
            backend/tests/**、docs/traceability.md
禁止修改目录：docs/spec/0.3-R10.md、tools/docstate/**、tools/discipline/**、
            backend/app/db/**、backend/app/events/**、backend/app/state/**、
            backend/app/plan/**、backend/app/lease/**、backend/app/verify/**、
            backend/app/merge/**、backend/app/git/**、docs/claims/ 下非本任务的文件
交付分支：agent/local/qoder/MVP-10b/a1
完成标准：
  1. Codex / Claude Code / OpenCode 三个 Adapter：各自的注册（harness_id、provider、
     adapter_version、registration_version 可追踪）与各自的 CLI 无头调用契约（argv 单点、
     有断言），全部实现 §7 接口（继承 HttpAdapter，未实现动词仍显式 UnsupportedVerb）✅
  2. 至少一个真实 Harness 完成 Claim / Heartbeat / Submit / Review ✅（实际三个都跑完了
     claim→heartbeat→publish→submit→verify→review→merge→merged）
  3. §25 阶段 5 退出条件逐条有测试 ✅（probe / Claim / Heartbeat / Submit / Verify /
     Push Proxy / 超时 / 恢复 / 危险操作拒绝，全部按 Adapter 参数化）
  4. 遗留项落地 ✅：复放 Claim 时对活跃未过期 Lease 轮换 token 并返回新明文
     （库中仍只存哈希；已过期/已释放不轮换）—— verify-mvp-04 登记转 10b 的那条
test_id：MVP-ADAPTER-002
测试命令：PYTHONUTF8=1 ./scripts.sh check
当前状态：done（作者完成，待合入；外部验收待认）
完成时间：2026-10-06 02:57
commit：622b1f2
验证结果：
  docstate    : 90 项检查，0 项失败
  discipline  : 42 项检查，0 项失败
  ruff        : All checks passed，73 files already formatted
  pytest      : 224 passed（MVP-10b 新增 29）
  关键断言     : test_real_adapters.py —— 三个真实 Adapter 各自的 argv 契约逐字断言
                 （claude 2.1.274 / opencode v2.0.22 本机 --help 核对；codex 未安装，
                 契约来自文档并在模块 docstring 注明）；每个 Adapter 走真实 HTTP 完成
                 claim→heartbeat（CLI 期间回执有断言）→publish(Push Proxy)→submit→
                 verify→review→merge→merged；超时→杀进程→abandon；重启丢 token→
                 复放 Claim 轮换新 token→继续同一 attempt（attempt_no 不增）；危险
                 操作拒绝（main 分支、不可达 commit、旧 epoch、四个无路由动词）；
                 SubprocessRunner 对真实子进程 kill-on-deadline 单独验证
已知问题：
  - **模型进程替换边界（须如实传播）**：多数用例用 ScriptedRunner 替身执行 worktree
    动作，真实模型进程未拉起（本机无 codex/有 claude/opencode 但 CI 不可依赖凭据与
    网络）。argv 契约、输出解析、SubprocessRunner（含 kill）均有独立断言补偿；
    「真实 Harness 完成全链路」的含义是：真实 Adapter 代码 + 真实 HTTP + 真实
    worktree/Push Proxy，模型进程以脚本替身注入。
  - **顺带关闭 10a 的两个静默缺口**：list_ready_tasks 与 read_context 此前方法存在但
    路由 404（10a 只记录了四个抛 UnsupportedVerb 的动词，这两个是查路由时发现的
    隐性缺口）。已补路由并逐 Adapter 断言；`visibility=redacted` 因 R10 §17 未定义
    语义而显式 422 拒绝，不猜测语义。
  - **门禁环境要求 PYTHONUTF8=1**：中文 Windows 下子进程管道默认 GBK，mergeguard CLI
    的中文输出与 test_merge_guard 的 UTF-8 断言在无此变量时 5 例失败。在 base
    （8934a75）上同样表现 —— 非本任务引入，属 tools/mergeguard/**（不在本任务允许
    目录）。建议 owner 在 cli.py 里 reconfigure stdout 为 UTF-8，或测试显式设 env。
    本任务的证据命令均带 PYTHONUTF8=1 并在上方注明。
  - 完成标准 3 里的「非本尝试分支拒绝」未单列用例：与「main 分支拒绝」同走
    publish.branch_not_allowed 同一分支守卫，main 用例已覆盖该守卫的拒绝行为。
  - claude 的默认 permission-mode 取 acceptEdits（不是 bypassPermissions）：unattended
    下 Bash/git 类工具可能因无审批失败超时，这是有意的保守默认，deployment 需要时
    由调用方显式覆盖（R10 §17 的安全审查可在 MVP-11 复核这一默认）。
~~~
