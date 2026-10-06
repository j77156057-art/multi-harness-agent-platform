### claims-split-review：复核认领登记拆分（✅ done）

~~~text
任务编号：claims-split-review
负责人 Harness：Qoder
开始时间：2026-10-05 21:08
依赖任务：verify-mvp-04（复核对象 847cc69 紧随其后合入 main）
工作区：.worktrees/qoder/
真相源引用：847cc69（复核对象）、TASKS.md 第 196 行违规记录（其纠正措施含序号断言）、
            H-4（每个 test_id 必须可追溯）、docs/dev-convention.md「约束的强制程度」
允许修改目录：TASKS.md、docs/claims/22-qoder--claims-split-review.md
禁止修改目录：tools/discipline/**、backend/**、docs/spec/**、docs/claims/ 下非本任务的文件
交付分支：agent/local/qoder/claims-split-review/a1
完成标准：
  1. 复核结论可追溯到具体文件与行号，不是印象
  2. 发现的问题连同「为什么门禁没抓到」一起写清，而不是只列现象
  3. 不代改拆分者的文件；需要动手的部分明确标出「需指派」
test_id：MVP-PROCESS-001
测试命令：python tools/discipline/discipline.py
当前状态：done
完成时间：2026-10-05 21:08
commit：见本任务提交
验证结果：
  复核对象实测：discipline 24/24 全绿的同时，序号 14/15 各重复一次、
  claim-nullable 存在两个逐字相同的认领文件 —— 即「全绿」与「不自洽」并存。
  四个 claim-less 提交的清单由 git log 逐个核对：32f4e45、9d3d3ef、d5769e2、847cc69。
已知问题：
  - 本任务只提交反馈，不修 ②③④。修复需一次明确指派（涉及 tools/discipline/** 与
    他人独占的 claim 文件）。
  - 序号 22 的选取依据：当前最大序号为 21（因重复，23 个文件只用到 21 个号）。
~~~
