# 认领：root-worktree-clean

<!-- OpenCode 独占。其他 AI 请勿修改本文件；改你自己的即可，永不冲突。 -->

| 字段 | 值 |
|---|---|
| task_id | `root-worktree-clean` |
| Harness | OpenCode |
| 认领时间 | 2026-10-06 00:35 |
| 分支 | `agent/local/opencode/root-worktree-clean/a1` |
| 状态 | ✅ done（代码侧完成；合入 main 待问题 6 耦合拍板 + 一次裸合并例外） |

任务表对应行：`TASKS.md` → 「任务表」章节，搜 `root-worktree-clean`。

## 变更记录

- 2026-10-06 11:30：序号 40 -> 53。main 已到 52（第 12 次撞号：40 早被
  `40-qoder--MVP-10a.md` 占用，中间又同步了 12 个提交）。
- 2026-10-06 11:20：按 TRAE 评审问题 1 收紧 `MERGE_HEAD` 豁免判据：
  「存在即放行」改为「存在 **且** 无未合并条目 **且** 合并锁持有者存活」。
  原判据在本组卡自己声明的头号场景里失效 —— 别人在根工作区留下合并现场时
  恰好满足「MERGE_HEAD 存在」，于是本组判 ok=True；手工写一个 40 字符的
  `.git/MERGE_HEAD` 就能让占用状态静音。
- 2026-10-06 11:15：按 TRAE 评审问题 2/3 **整体回退 `--skip-group`**（`cc4e4af`）。
  它不解决任何问题（父提交自带豁免已覆盖），却是无白名单/无理由/无鉴权的
  通用后门，且留痕只写 stderr 而 `gate.yml` 的 `--json` 只收 stdout。
  随之 `mergeguard` 也不再对 discipline 传该参数 —— 该修的是判据，不是开关。
- 2026-10-06 11:10：修 WorkBuddy 报告的 commit 误报
  （`52-workbuddy--report-mergeguard-commit-bug.md`）：git 间歇性写 index 失败时
  commit 退出码非零但内容已落地，原实现会让调用方误以为合并失败而重跑。
  新增 `_commit_landed()` 在报失败前核实（工作区干净 + HEAD 是 merge commit
  + **被合分支已是 HEAD 的祖先**）。

- 2026-10-06：序号 38 -> 40。Qoder 在 main 上占了 38（38-qoder--MVP-07.md），
  40 是当时 main 的最大值 +1。
- 2026-10-06：序号 36 -> 38（main 新增 36 撞号）。

- 2026-10-06：序号由 36 重编为 38（main 新增认领占用该号）。

- 2026-10-06 00:35：认领。
