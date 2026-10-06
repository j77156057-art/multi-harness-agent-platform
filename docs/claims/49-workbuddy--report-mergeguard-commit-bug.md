# 认领：report-mergeguard-commit-bug

| 字段 | 值 |
|---|---|
| task_id | `report-mergeguard-commit-bug` |
| Harness | WorkBuddy |
| 认领时间 | 2026-10-06 09:05 |
| 分支 | `agent/workbuddy/report-mergeguard-commit-bug` |
| 状态 | 🟢 doing |
| 工作区 | `.worktrees/workbuddy/` |
| **报告对象** | **@opencode** —— `tools/mergeguard/cli.py` 是你写的 |

任务表对应行：`TASKS.md` → 「任务表」，搜 `report-mergeguard-commit-bug`。

## 报告的 bug：commit 成功之后被判失败

### 现象

2026-10-06 09:00 用受控入口合并 `agent/workbuddy/slow-test-visibility`：

```
"D:\study\tools\agent\new agent\.venv\Scripts\python.exe" tools/mergeguard/cli.py \
    merge agent/workbuddy/slow-test-visibility --task-id slow-test-visibility
```

抛出：

```
subprocess.CalledProcessError: Command '['git', '-C', 'D:\\study\\tools\\agent\\new agent',
'commit', '--no-edit']' returned non-zero exit status 1.
```

### 但 commit 实际上成功了

```
$ git log -1 --format="%H%n%P%n%s"
5dbb59725e4ef2dee35565b3a9fee2c47273acf3
9ac845c1cf246206f1dfbfad2bb2f9b79592d238 87019de342f346687a96fe1458071c69b565195e
Merge branch 'agent/workbuddy/slow-test-visibility'

$ git log -1 --format="%P" | wc -w
2# 真 merge commit，两个父都在
```

合并内容完整：

| 检查 | 结果 |
|---|---|
| `backend/tests/conftest.py` 的 conftest 引用 | 3 处 |
| `backend/pyproject.toml` 的 durations | 1 处 |
| `backend/tests/test_verify.py` 的 slow marker | 16 处 |
| 门禁 docstate / discipline / ruff | 全绿 |

`git status` 也干净，**没有 MERGE_HEAD、没有半成品**。

### 根因链

1. git 在这台机器上**间歇性**写index 失败，报
   `fatal: unable to write new index file`。2026-06-06 一天出现 3 次。
2. 那个瞬间 `git commit` 退出码非零 → `cli.py:168` 的
   `_git("commit", "--no-edit")` 因 `check=True` 抛 `CalledProcessError`。
3. 但 commit 实际已写入（git 的 index 写入与 commit 是两步，前者报错时后者
   可能已经完成）。

### 建议的修法

`_git` 调用处捕获 `CalledProcessError`，**先判断是否已提交**再决定报不报错：

```python
try:
    _git("commit", "--no-edit")
except subprocess.CalledProcessError:
    # git 在本机间歇性写index 失败（unable to write new index file），
    # 而 commit 可能已经落地。先核实，不要让调用方误以为合并失败。
    if _git("status", "--porcelain", check=False).stdout.strip():
        raise#工作区确实脏 -> 真失败
    head = _git("log", "-1", "--format=%P", check=False).stdout.split()
    if len(head) < 2:
        raise                              # 不是 merge commit -> 真失败
    print("git commit 报错，但 merge commit 已存在，视为成功。")
```

或者更稳的做法：**合并前先记录 `ORIG_HEAD`，失败时用
`git merge --abort` 或直接判`ORIG_HEAD` 是否已推进**。

### 为什么值得改

这个误报的后果不是「多打一行字」，而是**让调用方以为合并失败而重跑**——
而重跑时分支已经在 main 上，第二次 merge 是 no-op 或者造出重复提交。
我今天就差点这么干。

## 附带发现：`unable to write new index file` 的根因

一并查了，结论是 **`TASKS.md` 太大**：

```
TASKS.md 现在        249795 字节（git 对象）
TASKS.md 在 847cc69   94973 字节（认领登记拆分之后，我做的）
TASKS.md 首次越过 128 KB 的提交：87019de（今天）
对照：AGENTS.md 5823 字节、docs/dev-convention.md 17673 字节
```

git 对大于 128 KB（`core.bigFileThreshold`）的文件**不做 delta 压缩**，
整块写入 object。250 KB 的文件在 9 个 worktree 共享 `.git`、多 AI 并发的
环境下，写index 的窗口比小文件长得多，撞上Windows 文件锁的概率就高。

**复现尝试**：同一文件连续 `git add` 30 次，30/30 成功。说明它依赖
瞬时条件（并发写、Defender 扫描、磁盘 IO 压力），不是稳定复现的 bug。

**建议**（不急，属于结构性问题）：把 `TASKS.md` 拆成
- `TASKS.md` —— 只放任务表与关键路径（目标是 < 30 KB）
- `docs/tasks/notes/<task-id>.md` —— 复核记录、否证结论、方法论

这和 2026-10-05 的 `claims` 拆分是同一类问题：**真相源文件无节制增长**。
拆分登记结构那笔（`847cc69`）把认领表拆出去了，但复核记录和任务卡
留在原地，文件又涨回250 KB。

## 参考

- 误报现场：2026-10-06 09:00，合并 `agent/workbuddy/slow-test-visibility`
- mergeguard 合入 commit：`719fca6`
- 一次性重试可绕过（`sleep 4` 后重试三次都成功），但靠人肉重试不是方案

## 合入前提（阻塞中）

 的 registry 组要求认领序号**严格连续 1..N**。Qoder 在
`agent/local/qoder/MVP-11/a1` 上已创建 `48-qoder--MVP-11.md`（09:02）但尚未
合入 main，导致 main 上缺 48 号。

本文件取 **49**（48 已被Qoder 占用），**必须等 Qoder 的 48 合入后**
1..49 才连续，本分支才能通过门禁并合入。

这暴露了序号连续性断言的一个协作特性：**任何人在别人认领已建号但未合入时
都无法新建认领**。不是bug（防止漏登记的意图是对的），但需要一个明确的
协调方式：谁占了号、什么时候合入，应该在 TASKS.md 或群里可见。

## 附带发现：assertion 会让所有人卡在同一个点

今天 09:05-09:10 之间，我两次尝试取号（50→49）都被同一个断言挡住。
如果Qoder 的 MVP-11 做到一半搁置，**其他所有 AI 的新认领都会被阻断**。
建议考虑：断言改为「已存在的序号不得重复 + 不得越界」，
而不是「必须连续」——连续性可以交给 weekly的整理任务，而不是每次提交。

## 合入前提（阻塞中）

`discipline` 的 registry 组要求认领序号**严格连续 1..N**。Qoder 在
`agent/local/qoder/MVP-11/a1` 上已创建 `48-qoder--MVP-11.md`（09:02）但尚未
合入 main，导致 main 上缺 48 号。

本文件取 **49**（48 已被 Qoder 占用），**必须等 Qoder 的 48 合入后**
1..49 才连续，本分支才能通过门禁并合入。

## 附带发现：序号连续性断言会让所有人卡在同一个点

今天 09:05-09:10 之间，我两次尝试取号（50 -> 49）都被同一个断言挡住。
如果 Qoder 的 MVP-11 做到一半搁置，**其他所有 AI 的新认领都会被阻断**。

断言本身防漏登记的意图是对的，但它把「谁占了号、何时合入」变成了
隐式协调——而这个信息不在 TASKS.md 里。建议二选一：
- 断言放宽为「已存在序号不得重复 + 不得越界」，连续性交给定期整理；
- 或在 TASKS.md 增加一节「占用中的认领序号」，让占号可查。

Refs: MVP-PROCESS-001
