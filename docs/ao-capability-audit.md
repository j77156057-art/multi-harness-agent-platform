# AO 能力核对报告（实现前偏差记录）

核对对象：`D://study//tools//agent//agent-orchestrator-main`（Agent Orchestrator 源码快照）
核对目的：验证 R10 §3 / §37.4 声称的 AO 能力是否真实存在，作为 `ao_managed_git` 宿主模式的
可行性前提
核对方式：全库源码检索（Go + Python + 配置），非文档转述
核对时间：2026-10-05

---

## 1. 结论摘要

| R10 声称的 AO 能力 | R10 的用途 | 核对结果 | 判定 |
|---|---|---|---|
| `head_check` / `dev_headcheck` | Verify baseline differential 的基础 | 全库无此符号 | **不成立** |
| `fencing_token` | 迁移为 `task_attempts.current_epoch` + `boot_id` | 无该字段 | **不成立** |
| `lanes` | 由调度中心 `resource_leases` 取代或镜像 | 全库无此符号 | **不成立** |

**三条前提全部不成立。** 但 AO 存在 R10 未提及、且对 MVP 更有价值的其他能力（见 §3），
最关键的是：**AO 已经有完整的 Git worktree 管理实现**，这直接影响 MVP-05 的设计。

---

## 2. 检索证据

### 2.1 `head_check` / `dev_headcheck`

~~~text
$ grep -rni "head_check|headcheck" .
（无输出）
~~~

R10 §37.4 称"AO `head_check/dev_headcheck` 作为 Verify baseline differential 基础"。
源码中不存在该函数、命令或配置项。**MVP 阶段若照 R10 实现 baseline differential，
AO 侧没有可调用的现成能力，必须由调度中心自己实现。**

### 2.2 `fencing_token`

~~~text
$ grep -rn "fencing" .
backend/internal/adapters/chatdriver/persistenthost/host.go:47:  // fencing ownership with the same stable launch identity used by ACP hosts.
backend/internal/cli/session_switch.go:56:            // identifiers, handoff payloads, artifacts, and generation fencing remain
backend/internal/daemon/daemon.go:974:               // migration and fencing reconcile that gate the port bind.
backend/internal/lifecycle/manager.go:362:            // start. Once released, normal generation fencing discards those signals.
~~~

四处命中**全部是注释里的泛用 "fencing" 一词**（进程启动身份、generation fencing、端口绑定
reconcile），没有任何名为 `fencing_token` 的字段或结构。

R10 §37.4 称"AO `fencing_token` 迁移为 `task_attempts.current_epoch` 并补 `boot_id`"。
**该字段不存在，无法迁移。** fencing 必须完全由调度中心从零实现 —— 这与 MVP-04 的
`CORE-FENCE-001` 结论一致，不影响 MVP，但 R10 §37.4 的表述必须修正。

### 2.3 `lanes`

~~~text
$ grep -rni "\blane\b" --include=*.py .
（无输出）
$ grep -rn "\blane\b" --include=*.go backend/internal
（无输出）
~~~

R10 §37.4 称"AO `lanes` 不再与 `resource_leases` 争夺权威，改为调用调度中心 claim API 或
镜像事件"。**AO 中不存在 lanes 概念。** 这条修订的前提描述有误 —— 不是"AO 有 lanes 需要
收编"，而是"AO 根本没有 lanes"，收编工作量为零。

---

## 3. AO 实际存在的能力（R10 未提及，对 MVP 有价值）

这部分是本次核对的正向发现。R10 §4 把 AO 列为参考项目时，只提到了会话委派、
报告 Claim/Ack/Release、自动化 Lease 和 worktree，没有落到具体模块。

### 3.1 完整的 Git worktree 管理（`backend/internal/adapters/workspace/gitworktree/`）

~~~text
commands.go      discard.go      parse.go      remove.go
remove_error_windows.go        remove_repair_windows.go
workspace.go     prepared_branch_test.go        observe_test.go
workspace_reclaim_test.go     workspace_preserve_test.go
workspace_integration_test.go
workspace_forcedestroy_test.go
~~~

**这直接影响 MVP-05 的工作量评估。** 值得注意的能力：

- `remove_error_windows.go` / `remove_repair_windows.go`：
  **Windows 专用的 worktree 删除错误处理与修复** —— MVP-05 的 worktree 回收一定会撞上
  Windows 上 `.git/index.lock`、Defender 实时扫描导致的文件占用问题。AO 已经踩过这些坑
  并留了修复代码，是可直接参考的实现样本（**注意许可证，需先确认**）；
- `prepared_branch`：预置分支，对应我们的 `publish_task_branch` 发布语义；
- `workspace_reclaim` / `workspace_preserve`：worktree 回收与保留策略，
  对应 R10 §14 的 worktree 生命周期；
- `workspace_forcedestroy`：强制销毁，对应 R10 §14 的异常回收出口。

**建议**：MVP-05 开工前先读 `gitworktree/remove_error_windows.go` 与
`remove_repair_windows.go`，能省掉一轮 Windows 特有的踩坑时间。

### 3.2 worktree 实际路径口径

~~~text
/Users/me/.ao/worktrees/01ABC
D://dev//agent-orchestrator//.ao//worktrees//demo//worker-1
~~~

即 `<项目根>/.ao/worktrees/<project>/<worker-N>`。**与 R10 §14 建议的
`wt/<短ID>` 物理目录口径不同。** MVP-05 需要决定是沿用 AO 口径还是用 R10 口径 ——
建议用 R10 的 `wt/<短ID>`（更短、Windows 路径更安全），但要在 `docs/state-projection.md`
类似的文档里记录这个偏离。

### 3.3 `sessionguard`：并发写入的正确范式

~~~text
Package sessionguard owns the one invariant every write into a live session's pane
must satisfy: re-read the session immediately before writing and refuse when the
paste could land somewhere only the user may act.
~~~

**"写之前立刻重读、状态陈旧就拒绝"** —— 这个模式正是 R10 §13 fencing 的语义，
但作用在 session 层而非 task 层。AO 把它收敛到一个包内统一处理，注释里明说
"so the stale-state check lives in one tested place instead of being re-derived
per call-site"。

**这是 R10 可以吸收的设计经验**：fencing 检查应该像 sessionguard 一样收敛到单一
可测试的入口，而不是散落在每个调用点。R10 §13 列了 4 个 fencing 执行点
（Push Proxy、pre-receive、OS ACL、拒绝审计），但没有像 AO 这样把"写前重读"作为
单一不变量来组织。MVP-04 实现 fencing 时建议对齐这个结构。

### 3.4 其他相关模块

| 模块 | 说明 | 对 MVP 的意义 |
|---|---|---|
| `autoreview/` | 自动审查 | 对应 MVP-07 的 review 环节，可参考 |
| `codexops/` | Codex 操作 | 对应 MVP-10b 的 Codex Adapter |
| `presence/` | 在线状态 | 对应 R10 §19 的 Harness 状态 |
| `processalive/` | 进程存活探测 | 对应 R10 §15 重启流程的 intent 对账 |
| `runfile/` | 运行记录 | 对应 R10 §16 的完成证据 |

---

## 4. 源码快照的元信息偏差

| 项 | 实际情况 | 影响 |
|---|---|---|
| 后端语言 | Go 1.27.1，`module github.com/aoagents/agent-orchestrator/backend` | R10 未描述 AO 的技术栈，不构成矛盾 |
| 前端 | 存在 `frontend/` 与 `desktop-version-floor.json` | 不影响 MVP |
| Git 历史 | **无 `.git` 目录** | **无法确认版本/commit**，无法追 R10 引用的是哪个版本 |
| 许可证 | `LICENSE` 存在（11339 字节） | 借鉴代码前必须先确认许可证类型 |

**`.git` 缺失是硬约束**：R10 §4 说"复制代码前必须确认许可证、版本和依赖"，但当前快照
无法确认版本。这条流程要求在 MVP 引用 AO 代码前必须补齐（clone 完整仓库或记录 commit SHA）。

---

## 5. 处置建议

| 项 | 处置 | 责任任务 |
|---|---|---|
| `ao_managed_git` 宿主模式 | MVP 阶段判定 **no-go**，改用 R10 §3 第三档 `patch_apply` | MVP-10 |
| R10 §37.4 的 AO 能力对照表 | 按本报告重写，不能照抄 R10 描述 | 阶段 0 交付物 |
| baseline differential | 由调度中心自实现，不依赖 AO `head_check` | MVP-06 |
| fencing / epoch | 完全由调度中心实现；建议对齐 AO `sessionguard` 的"写前重读"范式 | MVP-04 |
| lanes 收编 | 工作量为零（AO 无此概念），R10 §37.4 该条表述需修正 | R10 文档修订 |
| worktree Windows 删除 | 开工前先读 AO 的 `remove_error_windows.go` / `remove_repair_windows.go` | MVP-05 |
| 引用 AO 代码前 | 必须补齐版本证据（完整 clone 或 commit SHA）并确认许可证 | MVP-05 / MVP-10 |

---

## 6. 本报告对 R10 的反哺建议

R10 §37.4 有一条修订的前提描述有误（"AO lanes 需由调度中心取代"→ AO 根本没有 lanes），
且三条 AO 能力引用全部无源码支撑。这与 R10 复审发现的问题**性质相同**：
**文档声称的外部系统能力没有经过源码核对**。

R11 复审应增加一项检查方向：**文档中所有声称的外部依赖能力，必须有源码/文档证据链接，
或明确标注"未核对"**。R10 §29.1 的 C 方向（实现可行性）提到了"本地 Agent Orchestrator
的真实源码是否支持本文设计"，但 R10 自己没做这个核对就写进了 §37.4。
