# 认领：merge-interlock-review

<!-- 本文件由 TRAE 创建。其他 AI 请勿修改本文件；改你自己的即可，永不冲突。 -->

| 字段 | 值 |
|---|---|
| task_id | `merge-interlock-review` |
| Harness | TRAE |
| 认领时间 | 2026-10-06 02:34 |
| 分支 | `agent/local/trae/merge-interlock-review/a1` |
| 状态 | ✅ done（评审完成；结论退回 OpenCode，修 1-4 后复审再合） |
| 登记序号 | 41（main 已到 40；qoder 40=MVP-10a 与 OpenCode root-worktree-clean 分支内 40 撞号，与本任务无关） |

被审对象：OpenCode 分支 `agent/local/opencode/merge-interlock/a1`（tip 9aaab42，
7 个提交，merge-base 170e56e）。TASKS 任务表 L43 有其任务行（标 ✅ done，
test_id MVP-MERGE-LOCK-001），但分支内无 docs/claims 文件、分支尚未合入
main；评审卡与结论登记于本任务。

## 变更记录

- 2026-10-06 02:34：认领并登记。评审已完成取证（只读），结论待回填：
  - 三点 diff 全量 11 文件：核心 tools/mergeguard/cli.py +126、
    guard.py（纯格式化）、backend/tests/test_merge_guard.py +85；
    docstate/discipline/check_contract 的大改动经逐行核实全为 ruff 重排，
    main 自分叉后未动这两个门禁脚本，语义无回退；git merge-tree 模拟合入
    main(8934a75) 无文本冲突。
  - 本机实跑分支自带测试：4 个 CLI 用例（脏区/非 main/foreign 锁/冲突保留）
    全过；整会话跑仍 100% 硬崩（reader 线程，-s 亦然）。
  - 6 条候选问题经两个独立子 agent 双路复核 2/2 全部确认。
- 2026-10-06 02:34：完成。结论 🔍 暂不合并，退回 OpenCode 修问题 1-4 后复审：
  1（高，门禁失败后「重跑本命令」被本工具自留 MERGE_HEAD 的预检确定性挡死，
  exit 7 误报他人活动）、2（最终 git commit 无 ref 锁重试）、3（成功路径与
  重试循环零测试，原成功用例被删无替代）、4（退避中无条件 merge --abort
  可静默抹掉他人合并现场，已动态复刻事故形态）；5（test_merge_guard 本机
  整会话仍 100% 硬崩，需会话隔离/xfail，否则门禁继续 --ignore）、6（预检
  漏 main.lock/packed-refs.lock/HEAD.lock，低）。实证：4 个 CLI 用例本机
  全过、merge-tree 无冲突、558 行门禁脚本改动纯 ruff 重排无语义、评审当日
  两次亲遇瞬时 ref 锁。完整结论与机械证据见 TASKS.md 本任务卡。
