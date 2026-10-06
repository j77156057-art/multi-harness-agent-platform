# MVP 终审报告（MVP-11）

## 0. 报告说明

| 字段 | 值 |
|---|---|
| 报告日期 | 2026-10-06 |
| 作者 | Qoder（MVP-11） |
| 分支 / 基线 | `agent/local/qoder/MVP-11/a1`，main 基线 `5dbb597`（含 MVP-04~08、10a、10b、10b-verify 全部合入） |
| 范围 | MVP-04 ~ MVP-10b 的集成级验证 + 安全审查 + 状态恢复出口覆盖 |
| 明确排除 | **MVP-09（前端/UI）**：清单自身「依赖现实」条款允许整体后置到 MVP-11 之后（"验证目标是协调语义，不是界面"）。`MVP-UI-001` 不在本报告的机械门内 |
| 方法 | ① 机械校验脚本原始输出（§1）；② 清单九类交付逐类归属（§2）；③ 状态覆盖的可执行契约（§3）；④ 安全审查（§4）；⑤ 集成健康状态机盘点（§5）；⑥ 按 R10 §29.2 格式的问题清单（§6） |

R10 §29.3 要求终审报告必须包含机械校验脚本的输出，不能只写人工核对结论 —— §1 逐字附上。

## 1. 机械校验原始输出

### 1.1 全量门禁（`PYTHONUTF8=1 ./scripts.sh check`，2026-10-06，exit 0）

~~~text
docstate    : 结果: 90 项检查，0 项失败
discipline  : 结果: 42 项检查，0 项失败
ruff        : All checks passed! / 77 files already formatted
pytest      : 241 passed, 1 warning in 383.50s (0:06:23)
=== 全量门禁通过 ===
~~~

与基线（224 passed）相比净增 17 项：`test_security_scan.py` 6、`test_state_coverage.py` 6、
`test_real_adapters.py` +4（进程树杀灭 1 + 心跳被拒 ×3 参数化）、`test_git_worktree.py` +1
（深根长任务名）。

### 1.2 新增套件（MVP-11）

~~~text
backend/tests/test_security_scan.py   6 项：扫描器规则、completion/消息/findings 先于落库、
                                            全库 canary 检索、merge 闸门拒绝
backend/tests/test_state_coverage.py  6 项：21 非终态完整性、豁免集钉死、映射边合法+触发器+测试存在、
                                            两类豁免的反向守卫、blocked 与 changes_requested 出口补测
~~~

### 1.3 门禁环境说明（如实披露）

- 本报告的所有门禁证据均在 `PYTHONUTF8=1` 下取得。中文 Windows 的管道默认 GBK；不带该变量时
  `test_merge_guard` 的 5 个 CLI 用例会因编码而假红（base `8934a75` 同样表现，非本任务引入；
  在 UTF-8 控制台不复现）。治本位置是 `tools/mergeguard/cli.py`（不在本任务允许目录），
  已作为已知问题转交（见 §6-P3）。

## 2. 清单 MVP-11 九类交付逐类归属

| # | 交付 | 归属（file::test） | 状态 |
|---|---|---|---|
| 1 | 并发 Claim | `test_claim_concurrency.py::test_only_one_of_two_racing_claims_wins`（真并发 + BEGIN IMMEDIATE 写锁） | ✅ 既有 |
| 2 | 重复 Claim | `test_claim.py::test_duplicate_claim_request_returns_first_result_without_new_attempt`；`test_replay_reissues_a_live_lease_token` | ✅ 既有 + 10b 补强 |
| 3 | Lease 过期恢复 | `test_lease.py::test_stale_task_can_be_reclaimed_and_taken_over`；`test_adapter.py::test_a_stale_attempt_can_be_taken_over` | ✅ 既有 |
| 4 | 旧 epoch 拒绝 | `test_adapter.py::test_a_stale_harness_cannot_keep_working`（`fencing.epoch_stale`）；`test_real_adapters.py::test_a_stale_epoch_is_refused_through_a_real_adapter` | ✅ 既有 + 10b 补强 |
| 5 | Verify/Review/Merge 顺序 | `test_verify.py::test_failing_head_cannot_reach_review`；`test_merge.py::test_gate_refuses_without_an_approving_review` / `test_gate_refuses_a_commit_with_no_passing_receipt` / `test_verification_failure_never_reaches_the_gate` | ✅ 既有 |
| 6 | 直接 push 主分支拒绝 | `test_publish.py::test_direct_push_to_integration_is_rejected_by_canonical` + `test_any_client_push_is_rejected`（pre-receive 拒绝一切客户端 push） | ✅ 既有 |
| 7 | secret 扫描先于事件落库 | `test_security_scan.py`（4 项：completion/消息/findings 的"命中→零落库"，全库 canary 检索） | ✅ **本任务新增** |
| 8 | Windows 路径/长路径/worktree 清理 | `test_git_worktree.py::test_archive_verifies_the_bundle_before_recycling`（清理断言含只读对象）；`test_long_task_ids_do_not_lengthen_the_physical_path`（深根 + 90 字任务名端到端） | ✅ 既有 + **本任务新增** |
| 9 | 终审报告（本文件，含机械输出） | `docs/reports/mvp-final-report.md` | ✅ 本任务 |

## 3. 状态覆盖（CORE-STATE-COVERAGE-001）

可执行契约在 `backend/tests/test_state_coverage.py`：**完整性、边合法性、触发器存在性、
引用测试存在性、豁免集反向守卫**五件事都是机械断言，任一侧漂移即红。

21 个非终态：**14 个有已实现出口并映射到活的集成测试**，其余 7 个显式豁免（分两类）。

### 3.1 有出口的 14 态（状态 → 出口事件 → 下一态 → 证明测试）

| 状态 | 出口 | 下一态 | 证明 |
|---|---|---|---|
| draft | plan.approve / plan.reject | ready / draft_rejected | test_api_flow（批准、拒绝各一） |
| ready | claim | claimed | test_claim |
| verifying | verify_passed / verify_code_failed / verify_env_failed / verify_execution_timeout / differential_not_applicable | awaiting_review / changes_requested / verifying / escalated / awaiting_human | test_verify（各一） |
| claimed | start / release / lease_expire | in_progress / ready / stale | test_api_flow + test_adapter |
| in_progress | submit_result / await / request_help / dependency_missing / abandon | verifying / … / blocked / abandoned | test_adapter + test_api_flow + 本套件 |
| needs_help | release | ready | test_api_flow::test_owner_actions_over_http |
| blocked | release | ready | **本套件新增**（report_blocked → blocked → release） |
| changes_requested | release | ready | **本套件新增**（失败 verify → changes_requested → release） |
| awaiting_review | approve / request_changes | approved / changes_requested | test_merge + test_api_flow |
| approved | merge_start | merging | test_merge |
| merging | merge_success / merge_conflict / merge_conflict_needs_owner / merge_post_commit_failure | merged / approved / changes_requested / approved | test_merge（各一） |
| stale | reclaim / attempt_budget_exhausted | reclaiming / escalated | test_lease |
| reclaiming | take_over | claimed | test_lease |
| awaiting_human | human.resume_verify_not_applicable | verifying | test_verify |

### 3.2 豁免（显式记录，不许静默跳过）

**A 类：出口存在（§10 表内）但本 MVP 没有任何触发器 —— 机制属于后续阶段**

| 状态 | 豁免理由 | 后续归属 |
|---|---|---|
| draft_rejected | plan 修订流（plan.revise）未实现 | 阶段 3 计划修订 |
| ready_waiting_deps | 无依赖巡检器；dependencies_satisfied 无触发器 | 调度巡检（阶段 2） |
| queued | 无资源可用巡检器；现测试以人工 UPDATE 回到 ready（test_claim 中注明） | 调度巡检（阶段 2） |
| abandoned | reopen 是人工动作，尚无 human-ops 面 | §18 人类决策 |
| escalated | human.release / human.adjudicate 同上 | §18 人类决策 |

**B 类：出口有触发器但状态不可达 —— 探测器未实现**

| 状态 | 豁免理由 | 后续归属 |
|---|---|---|
| plan_drift | 无 plan 哈希漂移探测器 | 漂移检测特性 |
| impl_drift | 无 impl 哈希漂移探测器 | 漂移检测特性 |

反向守卫：一旦以上任一机制被实现（触发器出现在 `app/`），测试立刻变红，强制把状态
移入映射表 —— 豁免不会烂在那里。

## 4. 安全审查

| # | 项 | 结论 | 证据 / 说明 |
|---|---|---|---|
| 1 | secret 扫描先于落库（§17） | ✅ 本次落地 | `app/security/scan.py`；completion/findings/消息三入口；命中 → 422（`security.secret_scan_blocked` / `message.secret_scan_blocked`）+ `secret_scan_blocked` 事件（位置/规则/值 hash）；"先于落库"由全库 canary 检索证明 |
| 2 | 错误正文不泄密（prep D-8） | ✅ | scan 错误 details 仅 `scan_id`+`rule_name`（测试断言键集精确相等）；message 静态模板；响应体经断言不含命中片段 |
| 3 | secret 阻合并（§17/§21） | ✅ 本次落地 | Merge Gate 检查任务名下 `secret_scan_blocked` 事件，拒绝码 `merge.secret_scan_blocking`，fail-closed 先于任何写（test_security_scan 端到端断言集成分支未动） |
| 4 | `capabilities()` 身份形态 | ⚠️ 记录在案 | 身份是显式查询参数（无认证层，MVP-10a 已记录）；`list_ready_tasks` 的 `redacted` 值因 §17 未定义语义而显式 422 拒绝，不猜测 |
| 5 | loopback 绑定 + Host 头 | ✅ 既有 | R10 §5：非 localhost Host/Origin 拒绝（test_api_errors） |
| 6 | push 边界（单账户 ACL 降级） | ✅ 既有 | ADR-DEV-002：canonical pre-receive 拒绝一切客户端 push；alternates 越界拒绝（GIT-ACL-001/002） |
| 7 | Lease/token 语义 | ✅ | 只存哈希；复放轮换（旧 token 失效，10b 测试钉死）；心跳被拒路径有测试（R3，本任务补） |
| 8 | 进程边界（Windows） | ✅ 本次修复 | `SubprocessRunner` 超时杀整棵进程树（taskkill /T；POSIX 进程组），真子进程+孙进程测试（R2） |
| 9 | §17 后半段（`task_events.redact` + 加密归档） | ⛔ 未实现（记录） | 属"已落库后判定敏感"的机制，MVP 未列交付；见 §6-P3 |
| 10 | artifact / mention 扫描面 | ⛔ 无入口（记录） | 相关 API 面尚不存在（§6-P3），扫描器一旦有入口即可复用 |

## 5. 集成健康状态机盘点

代码内 `integration_health` 四态（`green / red / retesting / escalated`）：

- ✅ 已实现并各有测试：`green→red`（合并后失败检测）、`red→retesting`（revert 开始）、
  `retesting→green`（revert 成功恢复）、`red/retesting→escalated`（revert 失败）
  —— `test_merge.py` 四条 revert/health 用例；merge gate 在 red 时拒绝（有测试）。
- ⛔ 未实现（记录）：独立的 `reverting` 阶段态、`merge_failure_detected` 与 red 的区分、
  以及 **revert 下游反向闭包**（`upstream_invalidated_seq`、下游 Claim 冻结、merged 下游进
  escalated —— traceability `MERGE-REVERT-002` 的缺口）。属特性而非测试缺口，见 §6-P2/P3。

## 6. 问题清单（R10 §29.2 格式）

### P1-1：合法但无法执行的边 —— 无主状态 release 崩溃

~~~text
问题编号：P1-1
严重级别：P1（已修复）
对应章节：R10 §10（changes_requested 的 release 出边）、§15（事件身份非空）
问题描述：_owner_transition 以 guard.owner 为事件 actor，无主状态（changes_requested）下
          owner 为空 → store 拒绝空身份 → 合法边 release 在运行期崩溃。
复现或失败场景：verify_code_failed 进入 changes_requested 后调用 release → ValueError。
修改建议：owner 为空时记录 "unknown" 身份（v1 无认证层的事实记录），已落地并测试。
是否阻止：否（已修）。
~~~

### P1-2：长分支名 + 深根目录触发 Windows MAX_PATH，worktree 创建失败

~~~text
问题编号：P1-2
严重级别：P1（已修复）
对应章节：R10 §14（短 ID 物理目录）、§28（Windows 可运行）
问题描述：短 ID 只保护了 worktree 物理目录；gitdir 内 refs/heads/<branch> 仍嵌完整分支名，
          深根 + 长任务名（90 字）下 checkout -b 因 >260 路径建不了 loose ref 而失败。
复现或失败场景：见 test_git_worktree::test_long_task_ids_do_not_lengthen_the_physical_path
          （修复前失败：unable to create directory ... /refs/heads/agent/...）。
修改建议：canonical 与每个 worktree 启用 core.longpaths=true（扩展长度路径），已落地并测试。
是否阻止：否（已修）。
~~~

### P2-1：CLI 超时只杀直接子进程（TRAE R2）

~~~text
问题编号：P2-1
严重级别：P2（已修复）
对应章节：R10 §13（心跳/失联语义在 Adapter 侧）
问题描述：遗留进程树继续在 worktree 写文件，超时后状态不可控。
复现或失败场景：test_real_adapters::test_the_subprocess_runner_kills_the_whole_process_tree
修改建议：taskkill /F /T（Windows）/ 进程组 SIGKILL（POSIX），已落地。
是否阻止：否（已修）。
~~~

### P2-2：revert 下游反向闭包未实现（traceability MERGE-REVERT-002 缺口复核）

~~~text
问题编号：P2-2
严重级别：P2（记录 + 延期）
对应章节：R10 §21 revert 影响分析、§10 merging 出边
问题描述：上游 revert 后下游任务的失效传播（upstream_invalidated_seq、下游 Claim 冻结、
          merged 下游进 escalated）未实现；MVP-11 复核结论：属特性缺口，非测试缺口。
复现或失败场景：不适用（无实现即无现场）；记录在 traceability 与本节。
修改建议：随 revert 影响分析特性实现；届时把 WAIVERS 中相关状态移入映射并加集成用例。
是否阻止：否（延期，已显式记录）。
~~~

### P3-1：状态出口机制的阶段缺口（5+2 态豁免）

~~~text
问题编号：P3-1
严重级别：P3（记录 + 延期）
对应章节：R10 §10、§18、§20
问题描述：draft_rejected / ready_waiting_deps / queued / abandoned / escalated 五态的出口
          机制（计划修订、依赖巡检、资源巡检、reopen、人类裁决）与 plan_drift/impl_drift
          两态的探测器均不在本 MVP 范围；drift 两态因此不可达。
修改建议：随对应阶段实现；test_state_coverage 的反向守卫会强制更新映射。
是否阻止：否（显式豁免，见 §3.2）。
~~~

### P3-2：§17 后半段与 artifact/mention 扫描面

~~~text
问题编号：P3-2
严重级别：P3（记录 + 延期）
对应章节：R10 §17
问题描述：task_events.redact + 加密隔离归档（"已落库后判定敏感"）未实现；artifact/mention
          的 API 入口不存在，扫描暂无可接入面。
修改建议：随相应 API 面落地时接入 app/security/scan.py。
是否阻止：否。
~~~

### P3-3：门禁在 GBK 控制台的环境性假红

~~~text
问题编号：P3-3
严重级别：P3（已转交）
对应章节：工程约定（非 R10）
问题描述：无 PYTHONUTF8=1 时，中文 Windows 管道按 GBK 解码，test_merge_guard 5 例假红；
          base 8934a75 同样表现（非本任务引入）。UTF-8 控制台不复现（TRAE 验收复测认可）。
修改建议：tools/mergeguard/cli.py reconfigure stdout/stderr 为 UTF-8，或测试显式 env。
          该文件在本任务允许目录外，已转交（Qoder→TRAE 纸条 2026-10-06）。
是否阻止：否。
~~~

## 7. 结论

- 清单 MVP-11 九类交付全部有归属（§2），secret 扫描与两处 Windows/长路径修复为本次新增；
- CORE-STATE-COVERAGE-001：14/21 非终态有映射到活测试的恢复出口，7 态显式豁免并带
  反向守卫（实现即红），不存在静默跳过；
- 安全审查 10 项：7 项 ✅、3 项显式记录延期（无 P0，P1 两项已修复）；
- 全量门禁绿（§1），本任务新增 17 项测试（安全 6 + 覆盖 6 + 进程树/心跳被拒/长路径 5），
  另有 R2/R3/长路径三处修复；
- **失败可复现性**（清单完成标准）：所有管道失败以事件为事实源 —— `aggregate=task_id`、
  `payload.attempt_id`、`seq` 三要素齐备；错误信封携带 `task_id` 与 `event seq`（scan 错误
  另带 `scan_id`）；复现步骤 = 本节引用的测试命令或事件序列；
- **MVP-09（前端）按清单条款后置**，是唯一剩余的 MVP 面。

**结论：MVP 内核（MVP-01~08、10a、10b）达成终审条件，可进入下一阶段；剩余面清晰记录、无静默缺口。**
