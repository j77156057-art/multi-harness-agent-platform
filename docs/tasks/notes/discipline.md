### discipline：协作纪律检查器（done）

~~~text
任务编号：discipline
负责人 Harness：Claude Code
开始时间：2026-10-05 08:05
工作区：.worktrees/claude/
依赖任务：agent-onboarding（🟡 待合入）
真相源引用：docs/dev-convention.md「约束的强制程度」（本次新增）；
            参照 agent-orchestrator-main 的 docs/documentation-map.md 做法
允许修改目录：tools/discipline/**、TASKS.md、README.md、docs/dev-convention.md、
            scripts.sh、Makefile、.github/workflows/**
禁止修改目录：backend/app/**、backend/tests/**、backend/migrations/**、
            docs/spec/0.3-R10.md（只读）、tools/docstate/discipline.py 以外的逻辑
交付分支：agent/local/claude/discipline/a1
commit：a7adf3c（工具 + 门禁接线 + 强制程度清单）、
        be034b2（计数修正 + 同行双工具歧义守卫）
test_id：MVP-PROCESS-001
当前状态：done（已合入 main `6643868`）

交付：
1. `tools/discipline/discipline.py`：把纯自觉的登记规则变成会红的断言
   （registry 8 项 / cards 6 项 / counts 3 项，共 17 项，零外部依赖）
2. 并入 `scripts.sh check` 与 CI —— 复用已有的绕不过的门禁，不新增 git hook
3. `docs/dev-convention.md` 增「约束的强制程度」清单：
   已强制 / 靠自觉 / 根本没实现 / 已知盲区

### 验证结果

  docstate    : 88 项检查，0 项失败
  discipline  : 17 项检查，0 项失败
  ruff check  : All checks passed!
  ruff format : 13 files already formatted
  pytest      : 20 passed in 1.51s
  负向测试    : 把 README 两处 docstate 计数中的一处 88→87 → discipline 转红
                （exit 1，输出 `README.md 声明了错误计数 [87]，实际 88`），
                还原后转绿。断言有牙齿。

### 工具自身在开发中被查出 5 个缺陷（全部已修）

前 3 个在首次运行就暴露，后 2 个靠负向测试才暴露 —— 后两个如果没测就合入，
等于交付一条不会红的断言，而那比没有断言更糟：

1. `MVP-04 缺 Harness/分支` —— 规则 bug。状态列用字符串全等判定，
   `⏳ waiting ★瓶颈` 这种带注记的没被认作 waiting。
2. `MVP-03~11 未登记` —— 规则 bug且方向错了。没人认领的 waiting 任务本就不该有
   认领记录，我把要求定反了。改为只对已开工任务强制。
3. `MVP-02/-amend 缺验证证据` —— 解析 bug。卡片按 `### ` 切分到下一个标题就断，
   而证据在 `### 验收结果` 这类后续小节里，落在卡外。改为两遍解析：卡片范围延伸到
   下一张**卡片**的开头，中间的小节并入前一张。
4. `counts` 静默跳过 dev-convention.md —— 正则 `(\d+)\s*项(?:检查|断言)` 匹配不到
   「88 项**机械**断言」，而守卫写的是 `if declared and ...`，于是该文件被静默跳过。
   改为按行匹配（同时含 docstate 与项数的行）。
5. `counts` 并集判定放过撒谎 —— README 里 docstate 计数出现在**两处**，
   `actual in declared` 只要有一处对就放过。负向测试时我只改一处，检查仍然全绿，
   才发现。改为「所有声明的计数都必须等于实际值」。

### 设计取舍

- **工作区字段只对未完成的卡片强制**。该字段后加，不追溯改写历史卡片 —— 改写历史
  就是篡改，与本项目处理 86→88 计数时同一条原则。
- **验证证据接受两种形式**（`验证结果：` 多行块，或 `### 验收结果` 表格）。各卡片
  历史写法不统一，强行统一等于伪造记录。
- **只做结构检查，不做内容检查**。填了 `test_id` 但没真测，拦不住 —— 这一点写进
  `dev-convention.md` 的「已知盲区」，不假装覆盖到了。

**本任务自己又违反了登记规则第 0 条**（先加任务表行、后补认领登记），被自己写的
断言当场抓住。工具对自己生效，这是它唯一的可信度来源。
~~~

> **本卡已撤回**：TRAE 在 `5a1876e`（`agent/local/trae/r10-fix-10-card/a1`）已就同一
> 缺陷补卡并合入 main，其版本标题更准确（`R10-fix-10：task_claims 权威唯一键缺陷修复`），
> 且登记了独立任务 `R10-fix-10-card`。合入本卡会为同一 task_id 产生两张任务卡 ——
> 这正是本项目反复出现过的「两份真相」问题。**保留 TRAE 的版本，本卡作废。**
> 本任务（`gate-baseline-repair`）的实际价值只剩解除 WorkBuddy 的 detached HEAD，
> 以及下面记录的过程教训。
