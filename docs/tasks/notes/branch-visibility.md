### branch-visibility：未合入 main 的在途分支其认领是否已登记（done）

~~~text
任务编号：branch-visibility
负责人 Harness：Claude Code
开始时间：2026-10-05 22:22
工作区：.worktrees/claude/
依赖任务：tasks-critical-path（✅ 已合入 main）
真相源引用：TASKS.md 登记规则第 0 条与第 8 条；docs/dev-convention.md H-7
允许修改目录：tools/discipline/**、TASKS.md、docs/claims/**、README.md、
            docs/dev-convention.md
禁止修改目录：backend/**、docs/spec/0.3-R10.md（只读）
交付分支：agent/local/claude/branch-visibility/a1
commit：69a21b8
test_id：MVP-PROCESS-001
当前状态：done

### 动机：discipline 只读 main，看不见在途工作

其余断言读的都是 main 快照，所以「有人在分支上已经写了提交，而 main 的任务表仍写
`⏳ waiting`」完全查不到 —— 从 main 看过去那个任务像是没人做。触发场景：22:16 时
`agent/local/qoder/MVP-06/a1` 已有提交，而 main 上 MVP-06 仍是 waiting、`docs/claims/`
无对应文件。这是登记规则第 8 条的执行缺口：登记必须到 main 上可读才算登记。

与 merge 组**互补而非重复**：merge 组只看已合入的分支，本组看还没合入的。

### 关键设计：只对一种情形变红

| 分支有提交 | main 说 | 分支自己登记过 | 判定 |
|---|---|---|---|
| 是 | waiting | 是 | **不报** —— 正确流程的中间态，报红等于惩罚正确行为 |
| 是 | waiting | 否 | **变红** —— 开工前后都无处登记 |
| 否 | 任意 | 任意 | 不适用 |

### 开发中被查出并修掉的 4 个自身缺陷

1. **分支 task_id 解析错位** —— 按固定下标取 `parts[2]`，而实际格式是
   `agent/<group>/<harness>/<task-id>/<attempt>`（4 段）。更糟的是仓库里**两套命名
   并存**（早期 3 段 `agent/<harness>/<task-id>`），固定下标对 **40 个分支误报**，
   整条检查因此不可用。改为在路径段里找**已知** task_id，找不到就静默跳过。
2. **「task_id 都在任务表内」这条检查不可靠** —— 上一条的连带产物，直接撤掉。
3. **`ls-tree` 漏了尾斜杠** —— `git ls-tree <branch> -- docs/claims` 返回的是目录
   条目本身而非内容，导致已登记的分支被误判为未登记（Qoder 的 MVP-06 就被误报）。
4. **单测污染了另一个测试文件** —— 用monkeypatch 替换 `subprocess` 全局模块，
   `test_discipline.py` 的 git 测试随之全部报「unexpected git call」。改为按
   `check_merge(lister=...)` 的既有惯例加 `lister` / `out` 两个注入点，
   完全不碰 `subprocess`。

### 验证结果

  docstate    : 90 项检查，0 项失败
  discipline  : 42 项检查，0 项失败（branches 组 3 项为本次新增）
  pytest tools: 27 passed（原 14 项 + 本任务 13 项单测）
  单测覆盖    : 未登记→红 /已登记→绿 / 已开工→不适用 / 无提交→忽略 /
                驻留分支→跳过 / 3 段与 5 段命名都能识别 / 未知形状静默跳过 /
                lister 失败 fail-closed / 真实仓库只读集成

已知问题：
- **本组只在多分支的本地克隆下有牙齿**：CI 只检出当前分支，近乎空跑。这是设计取舍
  （跨仓库的可见性本就只能在本地查），已在 dev-convention 的强制程度表里写明
- 认领文件序号是从 main 的最大值往后取的，而其他 agent 在各自分支上也从同一个起点
  取 —— **两人并发会撞号**。本次我取 30，Qoder 在其 MVP-06 分支也取了 30；
  合并时由 registry 组的序号唯一断言抓 whoever 后合。根治需要全局序号分配器
  （TRAE 的 `message-global-seq` 似乎是同一问题的另一处修补）
- 本组只看「有没有认领文件」，不看认领内容是否与分支名一致
~~~
