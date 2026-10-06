### mergeguard-fix：续跑路径的「认错人」缺陷（✅ done）

~~~text
任务编号：mergeguard-fix
负责人 Harness：OpenCode
开始时间：2026-10-07 03:10（认领 59 号）
依赖任务：root-worktree-clean（其任务行所载）；实质承接 2026-10-07 03:05 实测事故
工作区：未登记（认领 59 号无此字段；本代笔不做推定）
交付分支：agent/local/opencode/mergeguard-fix/a1
真相源引用：tools/mergeguard 自身三原则（冲突不 abort、绝不接管别人的合并）；
            认领 59 号的实测复盘；docs/incidents/mergeguard-open-defects-2026-10-07.md
完成标准（摘自认领 59 号）：
  续跑路径必须证明「pending 记录与本次请求是同一次合并」，不能只比对
  merge_head 相等——否则会把别人的遗留合并当成自己的续跑（最坏：门禁恰好绿时
  把他人的合并提交成自己的）。有机械测试。
test_id：MVP-MERGE-LOCK-001
当前状态：done（合入 main；本卡由 Qoder 按协调者授权代笔，2026-10-07 05:5x）
完成时间：2026-10-07 04:35（合入）
commit：`2c85a9c`（合并提交；分支内实现提交 `a80c5ad` fix(mergeguard): 续跑必须
        证明「记录与请求是同一次合并」）
验证结果：
  - 合入时门禁：docstate + discipline 绿（当时的检查集下通过并提交）
  - 新增回归：backend/tests/test_merge_guard.py +137 行（合并路径的身份判据用例），
    随本次合并入 main
  - 代笔注：代笔未独立复跑该 137 行；以 main 合入后的全量门禁为准
已知问题：
  - 认领 59 号文件自身仍写 🟢 doing（本代笔的范围是卡与任务行；认领文件保持
    OpenCode 原文不动）
  - 与本卡无关、同一文件的其他缺陷（冲突路径不 stage、release 返回值被忽略）
    见 incident 文档，按需另行开单
~~~

> **代笔归因**：本卡由 Qoder 于 2026-10-07 05:5x 按协调者授权代笔。缘由：
> mergeguard-fix 已合入（`2c85a9c`）但缺卡、任务行未收敛，卡与 merge 两组
> discipline 判红，且 Qoder 自己的待提交合并现场已把全队合并冻住一小时。
> 内容全部取材于认领 59 号的原文与 git 记录，未添加任何未发生的验证声明。