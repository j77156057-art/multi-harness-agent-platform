# 认领：adapter-hardening

<!-- 本文件由 Qoder 创建。其他 AI 请勿修改本文件；改你自己的即可，永不冲突。 -->

| 字段 | 值 |
|---|---|
| task_id | `adapter-hardening` |
| Harness | Qoder |
| 认领时间 | 2026-10-09 00:12 |
| 分支 | `agent/local/qoder/adapter-hardening/a1` |
| 状态 | ✅ done（实现 e68c48f；待合入） |

任务表对应行：`TASKS.md` → 「任务表」章节，搜 `adapter-hardening`。

## 变更记录

- 2026-10-09 00:12：认领（协调者路线图第 3 项「Adapter 稳定性」）。清账 drill
  F3/F4/F9 三条：①codex 在 Windows 的 workspace-write 沙箱起不了 shell
  （`CreateProcessAsUserW failed: 2`）——把演练验证过的 `danger-full-access`
  收进适配器默认（nt）+ 显式披露安全边界；②opencode 由 python 直启不稳（后台
  服务 DB 报错）——适配器在 nt 上经 `bash -lc 'exec "$0" "$@"'` 壳启动（演练
  配方转正）；③claim replay 响应补 `worktree_path`，重启后的 harness 进程不再
  需要旁路查 workspaces 表（worker 侧接住）。范围=适配器/驱动面 hardening，
  不动状态机与表结构。
- 2026-10-09 00:40：实现完成并提交 `e68c48f`（config 两开关 + 两适配器平台
  分支 + ClaimItem.worktree_path + worker 接住 + README 环境要求小节）。
  真实 CLI 抽查（修复后 argv，披露配额）：codex 越过 F3 故障点、opencode 经
  壳建 session（F4 形态消失），两者止步于中继未监听（外部依赖，如实留档）。
  顺带修 test_sweeper_deps_resources 的冻结日期时间炸弹（与 frontend-token
  卡的导入时刻问题同族）。状态 → done。
