# ADR-DEV-002：MVP 阶段的单账户 ACL 降级

状态：**已接受**（MVP 阶段，**临时措施**）
日期：2026-10-05
关联任务：MVP-05
影响范围：`backend/app/git/**`、未来的 `scripts/**`（账户与 ACL 配置）、部署文档

---

## 背景

R10 §13 与 §17 规定 canonical Git 拓扑与权限必须落在**三个账户**上：

| 账户 | 拥有的东西 | 明确拒绝的 |
|---|---|---|
| `agent-scheduler` | state.db、canonical bare repository、凭证目录 | — |
| `agent-harness-<id>` | 自己的 worktree 与 gitdir | canonical 的 refs / objects / config / hooks、state.db |
| `agent-verify` | verify overlay | canonical 与 state.db 的写入 |

R10 §13 的原文是硬要求：

> Harness 账户对 canonical 的 refs、objects、config、hooks 和裸仓库文件系统没有写权限，
> Windows ACL 必须明确拒绝。

清单 MVP-05 允许 MVP 阶段降级，但要求**写明这是临时措施，以及阶段 2 前必须补齐的
三账户清单**。本 ADR 就是那个落点。

## 决策

MVP 阶段（阶段 0/1）运行在**单账户**下：调度中心、Harness、Verify 用同一个 Windows
账户。由 `settings.insecure_single_account`（默认 `True`，可用
`AP_INSECURE_SINGLE_ACCOUNT=0` 关闭标识）标记当前处于该模式。

**这是临时措施，不是设计。** 阶段 2 的退出条件包含「ACL 生效」，本 ADR 的存在就是
为了让这句话可核对。

## 降级之后，哪些边界仍然成立

单账户拿不到 OS 层的拒绝，所以本任务把可执行的那部分搬到了服务端与 Git 服务端，
而不是假装 ACL 还在：

| R10 要求 | 单账户下的替代执行点 | 证据 |
|---|---|---|
| Harness 不能直接 push canonical | canonical 的 `pre-receive` 钩子**拒绝一切客户端 push**；集成分支另给专门消息 | `test_publish.py::test_direct_push_to_integration_is_rejected_by_canonical`、`::test_any_client_push_is_rejected` |
| Harness 只能通过 Push Proxy 发布 agent 分支 | `publish_task_branch` 是唯一写入路径，且写入用调度器侧的 `update-ref`，不经过接收端口 | `test_publish.py::test_publish_imports_objects_and_writes_the_task_branch` |
| 旧 epoch 的发布必须被拒 | `guarded_write` 单一入口 | `test_publish.py::test_old_fencing_epoch_is_rejected` |
| alternates 只能指向本项目 canonical objects | 发布前校验 alternates 内容 | `test_publish.py::test_publish_rejects_alternates_pointing_outside_the_canonical_store` |
| 两个 attempt 不共享可写目录 | `workspaces` 的活动记录保证路径互斥 | `test_git_worktree.py::test_two_attempts_get_separate_writable_directories` |

**仍然不成立的部分（必须明确）**：单账户下 Harness **有能力**直接改文件系统 ——
它可以绕过 API 直接 `git update-ref` 写 canonical，也可以删 state.db。上面的钩子只
挡得住经过 Git 接收端口的写入。也就是说：

> 单账户模式下，「Harness 不越界」靠的是**约定与测试**，不是内核强制。
> 这恰好是 R10 §13 说"必须有实际执行点"时要排除的那种状态。

## 阶段 2 前必须补齐的清单

按依赖顺序，前两项是其余项的前提：

1. **建三个 Windows 账户**：`agent-scheduler`、`agent-harness-<id>`（每个 harness 一个）、
   `agent-verify`；凭证交 Windows Credential Manager（R10 §13），不落盘、不入库。
2. **canonical 与 state.db 的 ACL**：`agent-scheduler` 独占写；`agent-harness-*` 与
   `agent-verify` 明确拒绝（不是"未授权"，是显式 `Deny`，否则组继承会漏）。
3. **agent gitdir 的所有权**：`agent-harness-<id>` 拥有自己的 worktree 与 gitdir；
   gitdir 必须位于 canonical 之外（MVP 已按此布局：`.worktrees/` 之外的单目录 +
   `.git` 分离；`wt/<short>` 与 `wt/<short>.git`）。
4. **verify overlay 的隔离**：`agent-verify` 只读 canonical、只写 overlay。
5. **调度器对 Harness gitdir 的只读 ACL**（ADR-059 末段要求）。
6. **ACL 校验脚本**：把「尝试写 .git/config、hooks、refs、objects 必须被拒绝」做成
   可执行断言（R10 §16 的 verify 账户要求与 §26 的 GIT-ACL 系列）。**本 ADR 认为这是
   阶段 2 的退出条件之一，不是可选项** —— 没有它，前三项是否真的生效无法机械核对。
7. **关掉 `AP_INSECURE_SINGLE_ACCOUNT`**，并把 `--insecure-single-account` 从默认路径上移除。

## 与 GIT-PACK-001 的关系

`GIT-PACK-001` 要求「Defender 实时保护开启时跨账户 pack/bundle 传输成功或可对账失败」。
单账户下**没有跨账户**可言，所以本任务只覆盖传输与 intent 对账路径本身（对象确实从
agent gitdir 进入了 canonical，intent 状态可查），跨账户部分随第 1–3 项一起留到阶段 2。
这与清单要求一致：不得把这个缺口写成已覆盖。

## 后果

- 阶段 0/1 的功能开发可以继续，不必先搭账户体系；
- 阶段 2 的退出条件里，ACL 相关的部分**现在就有可核对的清单**，而不是一句"补齐 ACL"；
- 风险被显式接受并限定在单机开发环境：本 ADR 不适用于任何多用户或联网部署。
