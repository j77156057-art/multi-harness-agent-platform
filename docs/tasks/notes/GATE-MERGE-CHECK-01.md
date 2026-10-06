### GATE-MERGE-CHECK-01：已合入 main 的任务必须已登记、已完成、证据完整（✅ done）

~~~text
任务编号：GATE-MERGE-CHECK-01
负责人 Harness：TRAE
开始时间：2026-10-05 20:18
完成时间：2026-10-05 21:55
依赖任务：discipline（扩展既有检查器，不另起工具）
工作区：.worktrees/trae/
真相源引用：docs/dev-convention.md「约束的强制程度」（discipline 断言表与
            「靠自觉的：禁止在 main 上直接提交」）、TASKS.md 违规记录区三条
            （MVP-03 db9bc19、MVP-04 e656058、claim-nullable d5769e2）、
            tools/discipline/discipline.py、.github/workflows/gate.yml
允许修改目录：tools/discipline/**、.github/workflows/gate.yml、docs/dev-convention.md、
            docs/traceability.md、TASKS.md
禁止修改目录：docs/spec/**、backend/**
交付分支：agent/local/trae/gate-merge-check-01/a1
背景：三次「先合 main 后验收/缺卡」证明文档规矩在并行压力下没有机械执行点，
      discipline 只做 TASKS.md 快照自洽——main 带着红灯照样前进，红灯无阻断后果。
完成内容：
  1. discipline 新增 merge 组：`git log --merges` 解析默认标题合并提交里的任务分支，
     对照 docs/claims/ 认领文件/任务表/任务卡，已合入 main 的任务必须：
     (a) 分支已登记 (b) 任务表状态已完成 (c) 卡有 commit 与验证证据；
  2. registry 组新增认领序号 1..N 连续断言（唯一性 main 已有，本任务补连续）；
  3. git 不可用或浅克隆时 fail-closed（浅克隆会让 merge 列表为空而静默放行）；
  4. gate.yml checkout 改 fetch-depth: 0；
  5. dev-convention 把该规则从「靠自觉」移到「会红」，并写明直提 main 仍是盲区。
合 main 时的形态变化（重要）：
  实施期间 main 经 claims-split / claims-split-review / H-8 系列提交独立落地了
  32 项检查（认领拆 docs/claims/ 独占文件、task_id 去重、关键路径比对、
  共享文件 ownership、H-7 工作区断言）。合 main 解冲突时以 main 版检查器为准，
  仅移植本任务独有的 merge 组（6 项）与序号连续断言（1 项）；TASKS 清理项
  （认领重排、verify-mvp-04 命名）已由其他 harness 以不同方式完成，不重复落地；
  本任务原拟的 MVP-04-inject 改名未被 main 采纳（main 保留 verify-mvp-04），
  从之。test_discipline.py 同步适配 claims 目录结构。
完成标准：
  1. tools/discipline 单测覆盖：已合入未完成/未登记/缺证据各被拦，合规合并放行；
     浅克隆与无 git 环境 fail-closed；序号重复/不连续被拦；                    ✅
  2. discipline 在本仓库全绿（含 merge 组对真实 main 历史的核对）；             ✅
  3. docstate/ruff/pytest 不回归；README/dev-convention 的断言计数同步。         ✅
test_id：MVP-PROCESS-002
测试命令：.venv\Scripts\python.exe -m pytest tools -q；.venv\Scripts\python.exe tools\discipline\discipline.py
当前状态：✅ done
commit：1e6141d、724e013（原分支实现）；c244925（合 main 并移植到 claims 目录版）
验证结果：
  - discipline：39 项检查 0 失败（registry 13 含序号唯一+连续、cards 6、
    counts 3、ownership 2、deps 8、traceability 1、merge 6）；
  - merge 组对完整 main 历史的 fail-closed 核对：共 15 个已合并任务分支，
    全部已登记/已完成/卡有 commit 与证据；核对中抓出 MVP-08-prep 认领文件
    漏登实际结卡分支 a2（已补登并留痕），并修掉多分支字段被 strip 反引号
    误解析的检查器缺陷（新增 test_branch_tokens_multi_branch_cell 守卫）；
  - tools 测试：14 项全过（序号重复/缺号/合规、merge 组注入假 lister 的
    未登记/未完成/缺卡/缺证据/fail-closed 各路径、真实临时 git 仓库与
    浅克隆 fail-closed 集成）；
  - 不回归：docstate 90/0、backend pytest 113 项退出 0、ruff check+format 全过、
    MVP-08 prep 契约 FAILURES 0、变异 10/10。
~~~
