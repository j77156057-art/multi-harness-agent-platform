# 认领：tasks-split

| 字段 | 值 |
|---|---|
| task_id | `tasks-split` |
| Harness | WorkBuddy |
| 认领时间 | 2026-10-06 11:05 |
| 分支 | `agent/workbuddy/tasks-split/a1` |
| 状态 | 🟢 done |
| 工作区 | `.worktrees/wbsplit/` |

任务表对应行：`TASKS.md` → 「任务表」，搜 `tasks-split`。

## 任务

拆分 `TASKS.md`。当前 274891 字节 / 4259 行。

**理由是冲突面与可读性，不是性能。** 我开工时写的动机（275 KB 超过 git
`core.bigFileThreshold` 128 KB，导致 `git add` 间歇失败）**已被受控对照推翻**：
拆分前 279450 字节连续 `git add` 20 次全过，拆分后 24591 字节 8 次全过。
真正的机制是并发 git 进程抢同一个 `index.lock`（本机 IDE 与多个 harness 都有
常驻 git 轮询），与文件大小无关。详见 `docs/tasks/notes/tasks-split.md` 的
「撤回」一节，那里保留了否证记录。

仍然要拆的三条可验证理由：
1. **冲突面**：卡片与复核记录是两个最大的多写点，都是纯追加型内容，按 task
   一文件后彻底消失（2026-10-05 实测 19 个 merge commit 冲突 100% 集中于此）；
2. **可读性**：找某个 task 的卡从全文搜索变成直达路径；
3. **git 友好**：275 KB 单文件 → 53 个 1~11 KB 小文件，改动只重写少数 object。

## 动机：上一轮拆分只解决了一半

2026-10-05 的 `claims-split`（`workbuddy--claims-split.md`）只把**认领表**
从 TASKS.md 拆到 `docs/claims/NN-*.md`，复核记录、违规记录、任务卡全部留在原地。
文件从拆分时的规模又涨回 274 KB。**只拆一个多写点，不拆其余，等于没拆。**

真正的多写点是三个，git 冲突 100% 集中在这里：

| 多写点 | 所在节 | 字节 |
|---|---|---|
| 任务卡 | `## 任务卡` / `## 登记规则` / 两个 `## 提交前自检` 下 | 约 243000 |
| 违规/复核记录 | `## 认领登记` 下 9 个 `###` 小节 | 约 21000 |
| 认领登记表 | `## 认领登记` | 约 20000 |

前两个是纯追加型内容，一个 task 一份，互不引用，适合按 task 拆走。

## 拆法

- 留在 `TASKS.md`：状态图例、任务表、认领登记表、关键路径、prep、
  统一任务卡、提交前自检、占用序号节、共享文件改动声明区
- 移到 `docs/tasks/notes/<task-id>.md`：每个 task 的任务卡正文 + 违规/复核记录

`docs/tasks/notes/` 已由 2026-10-05 的拆分建好，本次只是把内容真正搬进去。

## 门禁影响：必须同步改 discipline

`discipline.py:177 parse_cards()` 直接从 TASKS.md 全文正则切 `### ` 块解析卡片。
卡片一旦移走，`check_cards`（6 条断言）与 `check_merge`（2 条断言）会全部判红。

**所以本任务不是纯文档搬家，必须同时改门禁**：`parse_cards` 改为读
「TASKS.md + `docs/tasks/notes/*.md`」两处，卡片身份仍以正文里的 `任务编号` 字段
为准（不靠文件名），这样任何一侧存在都能被认到，兼容性不退化。

## 完成标准

1. `TASKS.md` < 30 KB
2. `discipline` 全绿，断言数只增不减（44 → 至少 44）
3. 现有单测全过（`tools/discipline` 的测试文件）
4. `docstate` 90 项全过
5. 拆分后连续 5 次 `git add TASKS.md` 零失败

## 工作区

`.worktrees/wbsplit`（H-7：不碰根目录）