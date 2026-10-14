# FORMAL-DISPATCH-01

任务登记已于 `1ff750e3768f99e53911e2beae84ff666bc8762f` 合入 main，之后才实施。
当前交付分支 `agent/local/codex/FORMAL-DISPATCH-01/a1`，工作区 `.worktrees/codex/`。

## 行为

管理员经 enforce 认证注册 Harness；同版本同内容幂等，不同内容与旧版本回滚拒绝。
正式分派把不可变方案、明确成员授权与现有审批迁移放在同一事务，保存请求幂等结果。
领取校验目标/注册/能力/在线登记/容量，Claim 与 attempt 保存注册版本。
HTTP Claim 使用 scheduler canonical 的真实 integration HEAD，避免健康投影尚为 NULL 时丢失基线。
已有 base_commit 在返修领取时复用。

前端新增「正式分派」，服务端网关保管管理员令牌；不提供浏览器伪装 Harness 的路径。
网关限制回环上游、Host、Origin、写入头与项目组，任务差异读取先检查所属组。
操作员运行本机拉取 worker：真实 Codex CLI → Git commit → scheduler publish → submit → Verify。
提交前拒绝脏工作区、空差异、越出允许路径的提交。Windows 的 OS 沙箱限制仍按现有 ADR 披露。

## 实跑

前端：`D:/w/office-preview`；API：127.0.0.1:8848；页面：127.0.0.1:4174。
project/group：111 / office-formal；仅 Codex reg-001 接通。

第二条真实浏览器任务 `FD-aec39028dacb9e7d`：

- base `f82fd9965b7512d6f0dfcc3fd2fd94f68047176c`
- commit `721a1b1432c008ed6c6707322090ec184c4f670d`
- 唯一改动 `docs/formal-connection-confirmed.md`
- CLI exit 0；159.56 秒
- receipt `rc-ada5c36f51a643de9548c34d7cf9e5ca`
- receipt hash `sha256:da3f225b597b210cbb452332ba43e10b2bd29984377ddb4daf8fa8af359f6f82`
- baseline exit 1 / head exit 0；真实状态 awaiting_review；未批准、未合入。

首条任务 `FD-be07d00c09688673` 的 NULL 基线与原始 receipt 保留，不声称完整差分验收。
其提交的实际 diff 只有使用说明；旧 changed_files 列出的 README.md 来自无冻结基线时的错误比较。
后续任务修复基线，浏览器实测 files_changed=1；没有重写原事件/任务/receipt。

## 验证与证据

API/Claim/worker/既有 API 回归定点 36 项通过；网关隔离 HTTP 安全检查 10 项通过；
真实浏览器提交/模型/差分/回执/产物读取 7 项通过；前端 build 与旧 npm run verify 通过。
最终全量门禁记录见 `VALIDATION.md`（收口时填写）。

前端自身没有可用 Git 工作树，以本任务 handoff 的文件清单记录交付：
`D:/w/office-preview/handoff/codex-formal-dispatch-2026-10-14/`。
运行时证据在 Harness `data/formal-runs/` 与 `data/formal-evidence/`，不含凭据。

## 限制

当前 canonical 是独立种子仓库，任务写入 data/fw/ 下独立工作区，未导入 office-preview 代码。
formal-artifact 是结构验证，不能代替任务专属测试或人审。
此页面未实现最终 Review/Merge 操作，不会自动修改生产项目。
未接通其他 Harness；未冻结 ADMIN-01、未改变 ADMIN-03 的依赖。
未改历史美术、motion、manifest、hand_anchor、verified；不是全项目审计或生产验收。

## 过程问题保留

开始时草稿先于正式登记，已暂存并完成串行登记后再实施（登记卡保留记录）。
Windows mergeguard 进程探测存在 os.kill(pid,0) 副作用，登记前置修复及回归已在 main。
浏览器最终结果脚本首次误把 pre 内的 diff 当成外层 response.diff；修正脚本并复核同一任务，未为此重跑模型。
缺少 Git Bash，本轮运行 scripts.sh check 的等价 PowerShell 顺序：docstate → discipline → Ruff check/format → backend cwd 的完整 pytest；逐步检查退出码。
