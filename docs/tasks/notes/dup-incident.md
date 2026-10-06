### dup-incident：两份 MVP-10a 并行实现的事故记录（done）

~~~text
任务编号：dup-incident
负责人 Harness：Claude Code
开始时间：2026-10-06 03:38
工作区：.worktrees/claude/
依赖任务：无
真相源引用：TASKS.md 上方「违规记录：两份 MVP-10a 并行实现」；
            登记规则第 0 条与第 8 条
允许修改目录：TASKS.md、docs/claims/**
禁止修改目录：backend/**、tools/**、docs/spec/**（本任务只落文档，不改实现）
交付分支：agent/local/claude/dup-incident/a1
commit：e830e35
test_id：MVP-PROCESS-001
当前状态：done

交付：一条事故记录 + 一条任务表登记 + 认领文件。**不改任何实现代码。**

详细事实、对照表与三条建议见上方「违规记录：两份 MVP-10a 并行实现」。

一句话：认领当时 main 上 MVP-10a 确实无人认领，Qoder 在之后完成并交付；
我的整份实现（22 文件 1448 行）被全部丢弃，main 未被污染。

### 验证结果

  docstate    : 90 项检查，0 项失败
  discipline  : 42 项检查，0 项失败
  核验①       : main 上无 `test_adapter_fake`、无 `adapter/interface.py`、
                无 `docs/claims/40-claude-code--MVP-10a.md` —— 未污染
                （`git ls-tree -r --name-only main | grep` 三者皆空）
  核验②       : 本分支基于 `cf50b68`（main），`adapter/interface.py` 不存在 ——
                起点干净，未夹带被丢弃的实现
  本任务性质  : 只改 TASKS.md 与 docs/claims/，`tools/**` 与 `backend/**` 零改动

已知问题：
- 该事故**没有机制可自动拦下** —— `branch-visibility` 组设计上就不报「已在
  自己分支登记、仅未合入 main」的任务（怕惩罚正确行为），而正是那一档缺失
  让第二个 agent 无从察觉。三条建议都未实施，留给协调者裁决
- 被丢弃的实现（22 文件 1448 行）**不是保留在别处**——它随分支删除即消失。
  若日后认为其中某处有价值（如 `AdapterVerbMissing` 枚举全部 16 个 §7 verb 的
  做法），需要重新推导
~~~
