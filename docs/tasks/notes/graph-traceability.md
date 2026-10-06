### graph-traceability：依赖图机械校验 + 补 traceability.md（done）

~~~text
任务编号：graph-traceability
负责人 Harness：Claude Code
开始时间：2026-10-05 09:09
工作区：.worktrees/claude/
依赖任务：discipline（✅ 已合入 main）
真相源引用：docs/spec/mvp-task-checklist.md §0.1 依赖图、§0.2 关键路径、§2 各任务依赖；
            R10 §26 阶段检查 ID 体系；docs/dev-convention.md H-4
允许修改目录：tools/discipline/**、docs/traceability.md、TASKS.md、README.md
禁止修改目录：backend/**、docs/spec/0.3-R10.md（只读）、
            docs/spec/mvp-task-checklist.md（本任务只读不改）
交付分支：agent/local/claude/graph-traceability/a1
commit：5100c7f
test_id：MVP-PROCESS-001、DOC-PLAN-001
当前状态：done

交付：
1. 依赖图机械校验（deps 组 5 项）：依赖均指向已知任务、无环、任务表与清单 §2 一致、
   清单 §0.2 声明的关键路径必须是依赖图里的**最长链**且每一步都是真实依赖边
2. `docs/traceability.md`：登记 R10 §26 全部检查 ID 及**当前强制状态**，
   并加断言要求任务表出现的每个 test_id 必须在此登记

### 检查查出的真实缺陷（已修，非误报）

| # | 缺陷 | 性质 |
|---|---|---|
| 1 | §0.2 关键路径漏了 MVP-03，声明「7 个任务」 | §0.1 画了 MVP-03、§0.2 的线性路径漏了，**两节自相矛盾** |
| 2 | §0.2 关键路径止于 MVP-07 → MVP-11，漏了 MVP-10b | §2 规定 MVP-11 硬依赖 10b，**§0.1 的图本来就画对了，错的只有 §0.2 与 §4** |
| 3 | §4 写「10b 可以整体砍掉」，与 §2「MVP-11 汇总到 MVP-10」矛盾 | 已确认 **10b 在关键路径上**，改为「10a 优先于 10b」并说明如何重新获得砍掉它的余地 |
| 4 | §0.1 图里的 `MVP-03b` 是 0.3 时代的拆分残留，已无此任务 | 改为 MVP-03 |
| 5 | 清单用 `MVP-04～MVP-10`（全角波浪号），登记表拆成 MVP-10a/10b | 依赖悬空 `MVP-11→MVP-10`；两边统一为 `MVP-04~09 + MVP-10b` |

第 1、2 条合起来是关键路径 **7 → 9**。这类数字此前完全靠手写，
而项目自己的教训（§37.7 封闭规则第 1 条「allowlist 的任何数字不得手写」）
早就在 R10 里立过规矩 —— 同一类错误在依赖图上重演了一次。

### 工具自身的 3 个解析 bug（首次运行即暴露）

写断言前先探了清单的真实结构，探查结论直接推翻了最初的三个假设：

1. **只有 MVP-01 有 `依赖：` 行**，其余把依赖写在 `状态：...（硬依赖 X、Y）` 括号里，
   MVP-10 写在 `拆分：` 行。按「依赖：」行解析会得到一片「无依赖」。
2. **区间必须先展开再切分** —— 先按 `~` 切开 `MVP-04~10` 就只剩 04 和 10，
   中间的 05～09 会整个丢掉。清单同时用半角 `~` 与全角 `～`。
3. **边方向搞反了** —— `registry[task]` 存的是该任务的依赖，而链里 `a → b` 表示
   「b 依赖 a」，应查 `a in registry[b]`。方向错时全链都报「断裂」。

### 验证结果

  docstate    : 88 项检查，0 项失败
  discipline  : 24 项检查，0 项失败（registry 8 / cards 6 / counts 3 / deps 6 / traceability 1）
  ruff check  : All checks passed!
  ruff format : 13 files already formatted
  pytest      : 20 passed
  负向测试    : 把 §0.2 关键路径改回 7 个任务 → deps 组转红

已知问题：
- 清单的依赖表达是散文，解析基于正则；**若清单改写法，校验会误判而不是静默通过**，
  这比静默通过好，但仍是脆弱点。要根治需把依赖图改成机器可读格式（如 front matter）
- 本任务只做结构校验：不判断某个 test_id 是否真的覆盖了它声称的行为
~~~
