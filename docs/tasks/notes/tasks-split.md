### tasks-split：拆分 TASKS.md（✅ done）

~~~text
任务编号：tasks-split
负责人 Harness：WorkBuddy
开始时间：2026-10-06 11:05
完成时间：2026-10-06 12:05
工作区：.worktrees/wbsplit
依赖任务：claims-split
真相源引用：docs/claims/54-workbuddy--tasks-split.md
            TASKS.md 「任务卡」节（拆分后的位置说明与索引）
            tools/discipline/discipline.py check_cards（改为读两处）
允许修改目录：TASKS.md、docs/tasks/notes/**、tools/discipline/**、docs/claims/54-*.md
禁止修改目录：tools/docstate/**、backend/**、docs/spec/**、docs/claims/ 下他人文件
交付分支：agent/workbuddy/tasks-split/a1
test_id：MVP-PROCESS-001
commit：a7eca25（拆分主体）；本字段的回填随下一次提交
当前状态：done
验证结果：见下方「验证结果」小节
~~~

### 动机：上一轮拆分只解决了一半

2026-10-05 的 `claims-split`（`24-workbuddy--claims-split.md`）只把**认领表**从
TASKS.md 拆到 `docs/claims/NN-*.md`，复核记录、违规记录、任务卡全部留在原地。
文件从拆分时的规模又涨回 275 KB。**只拆一个多写点，不拆其余，等于没拆。**

真正的多写点是三个，git 冲突 100% 集中在这里：

| 多写点 | 所在节 | 字节 |
|---|---|---|
| 任务卡 | `## 任务卡` / `## 登记规则` / 两个 `## 提交前自检` 下 | 约 254000 |
| 违规/复核记录 | `## 认领登记` 下 9 个 `###` 小节 | 约 21000 |
| 认领登记表 | `## 认领登记` | 约 20000 |

前两个是纯追加型内容，一个 task 一份，互不引用，适合按 task 拆走。

### ⚠️ 撤回：拆分**不是** `git add` 间歇失败的原因

我在开工时写下的动机是：「TASKS.md 275 KB 超过 git `core.bigFileThreshold`
默认 128 KB，git 不对其做 delta 压缩、整块写 object，加剧
`unable to write new index file`」。**受控对照推翻了这个归因**：

| 条件 | 连续 `git add` | 结果 |
|---|---|---|
| 拆分前 279450 字节 | 20 次 | 20 成功 / 0 失败 |
| 拆分后 24591 字节 | 8 次 | 8 成功 / 0 失败 |

**大文件一样全过。** 所以文件大小不是那个间歇失败的变量。真正的机制是
**并发 git 进程抢同一个 `index.lock`**：本机 IDE 与多个 AI harness 都有常驻的
git 轮询进程（`tasklist` 实测同一时刻有 2 个 `git.exe`，PID 每次查询都变、
CPU 00:00:00，即瞬时轮询），它们与我的 `git add` 撞上时就报
`unable to write new index file`。**与文件大小无关，与并发有关。**

那为什么仍然做这次拆分？因为它有别的、可验证的理由：

1. **冲突面**：三个多写点里最大的两个（卡片、复核记录）都是纯追加型内容，
   按 task 一文件后彻底消失 —— 2026-10-05 那次 19 个 merge commit、冲突 100%
   集中在这两处，这是实测数据。
2. **可读性**：275 KB 的单文件里，「找某个 task 的卡」要靠全文搜索；
   拆开后是 `docs/tasks/notes/<task-id>.md` 直达。
3. **git 友好**：从单文件 275 KB 变成 53 个 1~11 KB 的小文件，
   每次改动只重写 1~2 个 object 而不是整个大文件。

**教训**：我把一个「同时发生的两件事」当成了因果。上次（2026-10-06 早）我也是
靠 `core.bigFileThreshold` 的文档说法推的，没有做对照实验。**症状归因必须做
单变量对照** —— 这次做了，结论是反的。原描述保留在本节里作为否证记录，
不再作为理由。

### 拆法

留在 `TASKS.md`：状态图例、任务表、认领登记表（含占用序号节）、关键路径、
prep、统一任务卡、提交前自检、共享文件改动声明区、任务卡索引。

移到 `docs/tasks/notes/<task-id>.md`：每个 task 的任务卡正文 + 该 task 名下的
违规/复核记录。一 task 一文件，永不冲突。

### 门禁必须同步改，否则拆分即失效

`discipline.py` 的 `parse_cards()` 原本**只从 TASKS.md 全文**正则切 `### ` 块解析
卡片。卡片一旦搬走，`check_cards`（6 条断言）与 `check_merge`（2 条断言）会全部判红
—— 门禁会误报「已合入 main 的任务缺卡」。

所以本任务不是纯文档搬家。改动：

1. `check_cards()` 增加 `repo_root` 参数，额外扫 `docs/tasks/notes/*.md`；
2. **判据不变**：卡片身份只认正文里的 `任务编号` 字段，不认文件名。所以拆分前后
   门禁认到的是同一批卡片，不会因为搬家而漏判，也不会因为文件名取巧而蒙混；
3. 新增一条断言「同一 task 不该有两张卡」—— 两侧同名时报出来而不是静默覆盖，
   因为那意味着有人改了 TASKS.md 里没搬干净的旧卡。

### 拆分过程中查出的真实缺陷

1. **两个 `## 提交前自检` 节**（L3417 与 L3529）：后者是历史追加时误用同名标题，
   底下 48 KB 全是任务卡。拆分时暴露 —— 此前没人注意到自己 append 的内容
   挂在了「自检清单」这个语义完全错的节下。
2. **任务表有一行被拼接坏**：`MVP-03-amend` 行尾接了 `|| graph-traceability | ...`，
   下一行又重复一次 `graph-traceability`。`discipline` 的 registry 组按行正则解析，
   坏行被当成正常行读进 task 表 —— **门禁没能拦住它**，因为拆分前那张表本身
   就是唯一的真相源，没人拿它对照任务卡。
3. **notes 文件名归属误判**：脚本按标题里的 task_id 匹配，而
   `### 复核记录：认领登记拆分（847cc69，Qoder）` 里没有 task_id，
   commit hash `847cc69` 中的 `6` 命中了 task 表里的序号列，于是生成了
   `docs/tasks/notes/6.md`。已手工归回 `claims-split.md`。
   **教训**：归属判据不能用子串命中，得先切出「标识符 token」再比对。
4. **占号撞号断言有个盲区**（门禁缺陷，非拆分引入）：原断言只比序号，
   不看 task_id。而 H-8 的正常中间态恰恰是「占号者在自己分支上建了
   `docs/claims/NN-*.md`，同时在自己 TASKS.md 里登记了 NN」—— 两者合并前必然并存，
   原断言把这个正确流程判红。改为：**同号同 task 不算冲突，同号不同 task 才报**。
   task_id 不靠列序猜（那张表是 `序号 | Harness | task_id | ...`，按列猜会取到
   `WorkBuddy`），直接从文件名 `NN-harness--task.md` 反解。
5. **我自己制造的注入残留**（过程事故，记录在此）：
   为验证「测试能不能真的失败」，我把 notes 读取改成 `if False and ...`，
   跑完证伪后从 `/tmp/disc.bak` 恢复 —— 但那份备份是在**改 task_id 提取之前**拷的，
   恢复时把注入改动一并带了回来。结果是「34 passed」的绿灯其实带着
   `if False`，被下一轮 `rm scripts/` 之后的门禁红给抓出来。
   **教训**：注入测试后的恢复必须用**注入前一刻**的备份，或直接反向编辑那几行；
   「先备份再注入再恢复」这个顺序本身就有坑 —— 备份点与注入点的先后要显式对齐。
   判据：任何 `if False and` / `True or` / 临时 marker 都应该在提交前 grep 一遍。

### 验证结果

- `TASKS.md`：**279450 → 24825 字节（-91%）**，低于 30 KB 目标
- `docs/tasks/notes/`：52 张卡，`comm` 双向核对与索引表**零差集**
- `discipline`：44 → **45 项断言全绿**（新增「同一 task 不该有两张卡」）
- `pytest tools/discipline`：**34 passed**（原 28 + 6 条新注入测试）
- `docstate`：90 项全绿
- `ruff check tools/discipline`：All checks passed

### 注入测试的证伪记录（不证明能失败的测试等于没测）

| 注入 | 期望变红的测试 | 实测 |
|---|---|---|
| `if False and os.path.isdir(notes_dir)` | 3 条 notes 相关 | **3 failed** ✅ |
| 撞号判据退回「只看序号」 | `test_reserved_slot_same_task_id_is_not_collision` | **1 failed** ✅ |

两组各自让对应测试变红，恢复后全绿。这证明 6 条新测试真的在测新逻辑，
不是恒真断言。

### 完成时间与 commit

完成时间：2026-10-06 12:05
commit：a7eca25