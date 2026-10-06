### root-worktree-clean-review：OpenCode root-worktree-clean 分支独立评审（✅ done，退回作者修问题 1-6 后复审）

~~~text
任务编号：root-worktree-clean-review
负责人 Harness：TRAE
开始时间：2026-10-06 03:42
依赖任务：merge-interlock-review（被审分支包含整条 merge-interlock，旧 6 条为继承项）
工作区：.worktrees/trae/
真相源引用：AGENTS.md §0 与 H-7（仓库根目录只用于合并与跑门禁）；
            被审分支作者卡（本文件搜 root-worktree-clean）；
            merge-interlock-review 评审卡（本文件 L3170 起）
被审分支：agent/local/opencode/root-worktree-clean/a1（tip f6080a5，
          13 提交，merge-base d76eb07；自有提交 96bef8a / cc4e4af / 107fc94；
          含整条未合入的 merge-interlock 9aaab42）
允许修改目录：TASKS.md（仅本任务卡与本任务表行）、
            docs/claims/42-trae--root-worktree-clean-review.md
禁止修改目录：tools/**、backend/**、docs/spec/**、其他 harness 的 claim 与分支
交付分支：agent/local/trae/root-worktree-clean-review/a1
test_id：无编号 test_id（基础设施评审；机械证据为临时 git 仓库探针 +
         detached worktree 实跑门禁与测试 + git merge-tree 合并树核查 +
         双路独立代码复核）
测试命令：detached worktree(f6080a5) 内 tools/discipline/discipline.py
          与 pytest tools；tools/docstate/docstate.py；
          tools/discipline/discipline.py（本分支 docs 变更后）
当前状态：✅ done（评审结论：暂不合并；退回作者修问题 1-6 后复审）
完成时间：2026-10-06 03:42
commit：e62a24b（结论提交；本行为随附回填提交）

背景与评审计划：
  作者意图：把「在共享主工作区做开发/留冲突」从「只挡住当次 mergeguard 合并」
  升级为「所有人的 discipline 全局变红」的持续施压；实现为 discipline 新增
  rootworktree 组（git rev-parse --git-common-dir 反推主工作区；
  .git/MERGE_HEAD 存在即放行；fail-closed），随后又加 discipline --skip-group
  并由 mergeguard 合并时豁免本组。评审方法：不采信提交信息，自有提交全量 diff
  逐行读 + 临时仓库多场景机械探针 + 分支 tip detached worktree 实跑 +
  独立子 agent 双路复核。

验证结果：（2026-10-06 03:42，基线 main cf50b68，被审 tip f6080a5；
不采信提交信息，自有提交 96bef8a/cc4e4af/107fc94 全量 diff 逐行读 +
临时 git 仓库五场景探针 + detached worktree 实跑 + 双路独立复核）

  总评：🔍 方向成立、common-dir 反推与 fail-closed 本机实证正确、新增测试
  稳定（tools 37 passed）；但问题 1 打穿该组自己声明的设计目的，问题 4/5
  决定分支现状合不进来，问题 6 要求先排合并顺序。退回修复后复审，不直接改
  作者分支。本分支应排在 merge-interlock 修复合入之后（见问题 10）。

  问题单（问题 1-3 经评审人与独立子 agent 同场景复现一致）：

  1. [中] MERGE_HEAD 一刀切放行，漏掉作者卡自己声明的头号场景。卡背景写
     动机是「Qoder、TRAE 轮流在根工作区写代码、留未解决冲突」；但他人遗留
     冲突合并（git status 为 AA/UU 且 .git/MERGE_HEAD 存在）时该组判
     ok=True 放行，承诺的「全局持续施压」在此形态下为零（mergeguard 入口
     预检仍 exit 7 挡合并，那是既有保护，不是本组功劳）。子 agent 另证：
     手工写一个 40 字符 .git/MERGE_HEAD 即可让占用状态静音。探针实证：
     真实双边冲突仓库 UU f.txt + MERGE_HEAD → ok=True；linked worktree
     视角同样放行；对照组普通脏改动正确判红。修法：豁免条件收紧为
     「MERGE_HEAD 存在且 merge-lock/owner.json 锁持有者存活」，或对
     未合并条目（AA/UU 等）数量>0 的情形不豁免。
  2. [中] cc4e4af 的 --skip-group rootworktree 对 mergeguard 门禁窗口
     冗余：96bef8a 自带的 MERGE_HEAD 豁免已让 merge --no-commit 后的门禁
     窗口判 ok（探针：干净 --no-ff --no-commit 后 MERGE_HEAD 在 → ok=True；
     不 skip 也是 1 项 0 失败）；对 do_merge 全路径穷举（预检失败/冲突
     return 4/ref 锁 abort 重试/--skip-gate/正常窗口/already up to date/
     门禁红 return 5）不存在「要跑门禁但 MERGE_HEAD 不在且根脏」的窗口。
     cc4e4af 的立论与父提交已存在的豁免自相矛盾，却引入通用后门：
     --skip-group 无组名白名单、无理由参数、无鉴权，实测对全部组重复传参
     可得 exit 0、0 项检查 0 失败。修法：问题 1 收紧后整体回退 cc4e4af；
     若坚持保留，至少加白名单 + --skip-reason 必填 + skipped 进 JSON 体。
  3. [中] --json 留痕对机器失效：{"skipped":...} 只写 stderr，stdout JSON
     体只有 total/passed/failed/checks，total 静默少 1；.github/workflows/
     gate.yml 正是 --json 重定向只收 stdout，自动化消费方无法知道少跑了
     断言（实测 stdout 键集合确认；stdout+stderr 合并的消费方则会拿到
     一行 JSON 加一段 pretty JSON，两种 parse 都坏）。
  4. [中] 生命周期未完成，现状不可合：任务表行、任务卡当前状态、claim 40
     状态三处均为 🟢 doing，卡 commit 未回填。discipline merge 组规定
     「已合入 main 的任务在任务表均已完成」「完成任务卡均填 commit 与验证
     证据」（tools/discipline/discipline.py L919/L942），现状合入即红。
     需翻 done 并补验证证据与 commit 后才具备可合状态。
  5. [中] claim 撞号：git merge-tree 模拟合入 main(cf50b68) 无文本冲突
     （合并树 d7d83b8），但树内 40-opencode--root-worktree-clean.md 与
     40-qoder--MVP-10a.md 并存，registry 重号必红；main 另有 41 号。
     本评审卡合入将占 42，作者重编号请取 43，并同步改任务表行、任务卡、
     claim 文件名与变更记录四处引用（该分支已有两次重编号经验）。
  6. [中/操作面] 合入即时效应：分支 discipline 实跑 43 项 1 失败，红的是
     rootworktree 组，如实报告当前主工作区有他人 WIP
     （M backend/tests/test_verify.py；?? backend/tests/chain_template.py）；
     --skip-group rootworktree 后 42/0。该分支一合，在根工作区清干净前
     所有 harness 的 check 与 mergeguard 合并全部被堵。强制函数是作者
     刻意设计、卡也写了退路（降级告警/仅根区运行时断言），但「是否接受
     这个耦合」需团队拍板，且合并顺序必须排在清根之后。
  7. [低] 未知/空白组名仍打印「[skip] 豁免组：typo（…）」：Result.skipped
     按实际剔除记账，提示行却用命令行原始值，两套账（实测
     rootworktree_typo 一条不少仍显示豁免；" " 显示空豁免）。
  8. [低] 豁免理由是固定占位文案「由调用方声明理由」，mergeguard 从不传
     理由（理由只存在于 cli.py 源码注释），「调用方须自行声明理由」无机制
     承载；如保留 skip 需加 --skip-reason。
  9. [低] --separate-git-dir 布局下 common-dir 父目录反推错误会假红，
     .git/MERGE_HEAD 拼接也同时失效（子 agent 实测；本仓库标准布局+linked
     worktree 不受影响）；--path-format=absolute 需 git ≥2.31（本机 2.55
     无影响，旧环境会全员 fail-closed 假红）。

  10. [继承项，不属本分支增量] 分支包含整条未合入的 merge-interlock
      （9aaab42），merge-interlock-review 卡（本文件 L3170 起）的 6 条
      问题原样带入；backend/tests/test_merge_guard.py 相对 9aaab42 零行
      diff，本机整会话硬崩/门禁靠 --ignore 的问题延续。建议 interlock
      修复合入 main 后本分支 rebase，再处理本单 1-9。

  做对的部分（复审确认）：common-dir 反推在含空格路径（new agent）+
  8 个 linked worktree 的真实仓库全部正确，从 linked worktree 运行确实
  查到主工作区；无 git/无法定位时 fail-closed 与声明一致；新增 11 个
  discipline 用例覆盖干净/脏/未跟踪/合并中/linked worktree/fail-closed/
  组归属/skip 语义，pytest tools 37 passed 本机稳定（不像 merge_guard
  那套会崩）；READ-FROM-QODER.md 纸条已在 107fc94 按约撤出；merge-tree
  无文本冲突。

  评审后门禁（本评审分支仅 docs 变更）：docstate 90/0、discipline 42/0。
~~~
