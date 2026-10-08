# Codex＋OpenCode 真实联调 + 故障演练 + 前端追溯（e2e-harness-drill / OPS-DRILL-001）

日期：2026-10-08 ｜ 作者：Qoder ｜ 性质：**证据报告**（演练脚本 gitignored；命令可复跑）

## 0. 环境与配方（如实）

- **codex**：不在 PATH（桌面应用包内 `AppData/Local/OpenAI/Codex/bin/codex.exe`，
  0.130.0-alpha.5）；模型调用经用户本机 relay `~/.codex/geekspace-proxy.mjs`（8402，
  relay=dbkj，含明文密钥——未复制入库/报告）。
- **opencode**：v2.0.22（npm 全局，原生 exe）。默认模型区域受限（403）——已获
  协调者授权为其新增最小 provider 配置指向 relay（`~/.config/opencode/opencode.json`，
  仅新增，未改其它）。
- **演练配方 vs 平台契约**（差异都在这，别混淆）：
  1. codex 演练用 `--sandbox danger-full-access`（默认契约 `--full-auto` 在本机
     起不了 shell，见 F3）；提交命令刻意**不带引号/空格**（F3 引号拆解）。
  2. opencode 演练经 `bash -lc 'exec "$0" "$@"'` 包裹启动（直启通道坏，见 F4）；
     适配器 argv 本体不动。
  3. 演练实例 env：`AP_PORT=8860 / AP_LEASE_SECONDS=120 / AP_HEARTBEAT_SECONDS=15 /
     AP_RECLAIM_GRACE_MINUTES=1 / AP_CONCURRENT_WORKTREE=32 / PYTHONUTF8=1`。
  4. 靶场=独立 canonical 仓（小 python 项目 calc.py/test_calc.py）；verify 判据
     用 `python -c` 差分检查；review/merge 用平台真实 `VerifyWorker`/`MergeWorker`/
     `review_claim+submit_review`（操作面脚本驱动，见 F6）。
- 6 个任务 × 2 harness 的真实模型调用全部经上述 relay；期间 relay 高抖动
  （502/ECONNRESET/ETIMEDOUT 间歇，见 F13）。

## 1. 全流程结果（认领→启动→心跳→完成报告→Verify→独立 Review→Merge Gate）

| 任务 | harness | attempt | 产出 commit | verify | review（异 provider 身份） | merge | 备注 |
|---|---|---|---|---|---|---|---|
| T-CDX2 | **codex** | a1 | `369fd83`（sub+test_sub） | passed rc-6bc8cd | opencode ✓ | **merged** → `2c063d64` | 首跑即全链 |
| T-OC | **opencode** | a4 | `14022db`（mul+test_mul） | passed rc-19fead | codex ✓ | **merged** → `555316b6` | 含 a1-a3 失败史（见 F4/F13）+ merge 恢复（F-merge） |
| T-ISO-A | codex | a1 | `f4824a3`（iso_a.txt） | passed rc-919512 | opencode ✓ | **merged** → `10870983` | 与 B 并发 |
| T-ISO-B | opencode | a1 | `e0414b9`（iso_b.txt） | passed rc-b5a93d | codex ✓ | **merged** → `f5fc4d1c` | 与 A 并发 |
| T-RESTART2 | opencode | a1 | `87eeffd`（restart.txt） | passed rc-adc987 | codex ✓ | **merged** → `21b652c7` | 正常链 |
| T-RESTART3 | codex | a4 | 重启重放后 submit（seq 92） | passed rc-657950 | opencode ✓ | **merged**（integration 无变化） | **重启恢复闭环**；空产出观察见 F11 |
| T-CDX | codex | a1 | 空（未提交） | **verify_code_failed** → `changes_requested` | — | — | 平台正确拦住空产出；**返工死端**见 F1 |

事件链实例（T-CDX2，全链 8 事件）：`plan.approve → claim(codex) → start →
submit_result → verify_passed(verify-worker-1) → approve(opencode) →
merge_start → merge_success(merge-worker-1)`。集成分支按序累计 4+2 个 merge commit。

## 2. 故障演练六项

| # | 场景 | 注入 | 平台反应（原始证据） | 结论 |
|---|---|---|---|---|
| 1 | **并发抢同一任务** | codex 与 opencode 两进程同时 claim T-RACE | opencode `claimed`（a1/epoch1）；codex `claim.already_owned` 拒 | 恰一胜者 ✓ |
| 2 | **进程退出** | codex/opencode worker 在 CLI 运行中 `taskkill /F /T`（3 轮）；另一轮 claim 后死（T-CRASH，容量闸死） | 心跳停 → 租约到期 → `lease_expire` 事件 → `stale`；死进程侧无任何提交 | ✓（含 T-CRASH/T-RACE/T-RESTART 三宗） |
| 3 | **租约过期** | 真实时钟（lease 120s）+ 真实巡检器 `python -m app.scheduler --once` | 首次 `actions=3`（T-RACE/T-CRASH/T-RESTART→stale）；**二轮同钟幂等 actions=0** | ✓ 含幂等证据 |
| 4 | **旧 token 写入** | T-RACE reclaim+take_over 后（epoch 1→2）用旧 epoch1 token 打心跳 | **409 `fencing.epoch_stale`**（"fencing epoch 1 is stale…(current 2)"）；对照组新 epoch2 token → **200** | ✓ |
| 5 | **重启恢复** | CLI 运行中杀 uvicorn（×4 轮）→ worker 失联退出 → 重启 server → **同 claim_request_id 重放** | 重放返回**同 attempt**（replay_worktree 挂回同目录）；CLI 续跑；最终 T-RESTART3.a4 完成 submit 并全链 merged | ✓（前 3 轮完成段被 relay 抖动阻断，第 4 轮闭环；F13） |
| 6 | **worktree 隔离** | T-ISO-A(codex)+T-ISO-B(opencode) 并发 | `distinct_paths/branches=true`；A 目录无 iso_b.txt、B 目录无 iso_a.txt（双向）；两分支独立 publish/merge | ✓ |

## 3. 前端追溯（结构快照口径；截图不可用见 §6）

- **群聊**：`operator` + `2026-10-08 02:53:51` + 两条联调消息 ✓。
- **任务列表**：10 任务实时状态（5×merged / changes_requested / 3×stale / abandoned）✓。
- **任务线程（T-CDX2）**：8 事件全链带**负责人 + 时间戳**（本卡新增时间列）：
  `claim(codex) 01:15:17 → start 01:15:18 → submit_result 01:16:25 →
  verify_passed 01:18:07 → approve(opencode) 01:18:08 → merge_success 01:18:10`；
  `approve` payload 展开 = `review_id/reviewed_commit 369fd83/findings_hash` ✓。
- **diff 卡**（diff-message 协同）：T-CDX2 显示 `4 文件 +13 −5 8dfabfd..369fd83`，
  `.pyc` 正确标"二进制"；T-CDX 诚实显示"0 个文件/与基线相比没有差异" ✓。
- **当前页 console 零消息、网络 7/7 全 200**（5174；5173 是他人实例，未触碰）。

## 4. 发现清单（F1-F13）

- **F1（高，平台缺口）：返工链未实现**——`changes_requested → claimed` 的
  `claim_for_revision`（R10 L449/523）与 `resubmit` 只有事件常量和迁移表边，
  **无任何实现**：T-CDX verify 失败后重领被 `claim.already_owned` 永久挡住
  （`current_attempt_id` 不清）。任何 verify 失败目前都是死端（只能人类
  reopen 换任务）。建议优先立卡（体量≈一张中等卡）。
- **F2（已修）：worktree alternates 被写成 CRLF**——git 不剥尾 CR，导致 worktree
  内每条 git 命令都报 `object directory ... does not exist`（codex/opencode
  CLI 实测刷屏；此前靠 `.strip()` 断言漏网）。修复=`newline="\n"`
  （`app/git/worktree.py`）+ 字节级断言钉死（变异验证：去掉即红）。
- **F3（高，适配器契约）：codex 在 Windows 的 workspace-write 沙箱起不了 shell**
  （`windows sandbox: runner error: CreateProcessAsUserW failed: 2`）+ 模型生成的
  带引号命令经 cmd 被拆（`pathspec 'sub''`）。默认 `--full-auto` 的修正（或环境
  前置检查+文档）与 `--add-dir/writable_roots` 方案需另立卡评估安全边界。
- **F4（高，适配器集成）：opencode 由 python 进程直启不稳**——报
  `Database is not empty and has no session table`（后台服务模式，linked
  worktree 必现）或长令触发 usage dump；经 bash 壳稳定（配方）。根因未完全定位
  （非 HOME 形态）；适配器/驱动面需硬化或给出环境要求。另：opencode 单次
  provider 错误即退（codex 有 5 次重试）——relay 抖动下差异明显（F13）。
- **F5（中）：`review_policy=cross_provider` 无强制**——只存字段；本次演练用异
  provider 身份自证，但平台不拦同 provider 自审。
- **F6（中，部署面）：verify/merge 无生产驱动进程**——`VerifyWorker`/`MergeWorker`
  只有测试与本次操作面脚本在驱动；sweeper 只管队列状态机。认证/runtime 卡可带上。
- **F7（中）：worktree 无回收**（human service 已声明属 operator 缺口）→ 容量闸
  （默认 8）被历史 attempt 占满后新 start 全被拒（本次 T-CRASH/T-RESTART 各撞一次，
  演练实例最终抬高到 32）。
- **F8（低）：`CliHarnessWorker._tick` 只把 `AdapterError` 映射为
  HarnessLeaseLost**——传输层 `ConnectError`（server 重启场景）裸漏为未知异常。
- **F9（低）：claim replay 响应不含 worktree 路径**——重启后的 harness 进程找不回
  worktree（演练从 workspaces 表取）；部署面口径需写清。
- **F10（低-中）：codex `exec --json` 的 `turn.completed.usage` 带真实 token
  （本次 21k-221k input），我们的适配器 `parse_output` 未解析**——免费的真实
  预算数据源，接线即可（budget-guard 的成本源缺口）。
- **F11（观察）：空产出可通过"存在性"判据**——T-RESTART3 的验收命令只查
  `restart.txt` 存在，而基线（T-RESTART2 已 merge）已含该文件 → 空提交也
  verify_pass+merged（merge 无变化亦报 merged）。靶场判据设计教训：**验收要含
  delta 语义**。
- **F12（环境）：本机 AV/文件锁高发**——git index/FETCH_HEAD/目录句柄随机
  `Permission denied`/`unable to write`（R10 §23 已预警 Defender）；演练脚本
  全部带重试，平台侧代码未改（除 F2）。
- **F13（环境）：relay 高抖动**——502/ECONNRESET/ETIMEDOUT 间歇；codex 重试可扛，
  opencode 单败即退；重启演练前 3 轮完成段均被它阻断（第 4 轮通过）。属用户
  基础设施，非平台缺陷。

## 5. 复跑指南（摘）

```bash
# 0) 前提：relay 在跑（~/.codex/geekspace-proxy.mjs，8402）；codex 在包内路径；
#    opencode provider 已指向 relay。
cd backend && source data/e2e-drill/env.sh        # 见 env.sh（gitignored）
"$PY" -m app.db && "$PY" data/e2e-drill/setup.py  # 实例+靶场仓+注册
"$PY" -m app.main &                               # 8860
"$PY" data/e2e-drill/bootstrap.py                 # 项目/群/计划/health
# worker：codex 走 PATH 前置 bin；opencode 走 bash 壳（配方在 worker.py）
"$PY" data/e2e-drill/worker.py codex T-CDX2 cr-x1 --timeout 480
"$PY" data/e2e-drill/drill_ops.py verify T-CDX2 sub-check   # review/merge 同族
# 故障演练各步见 §2 表内命令（scheduler --once / taskkill //T / heartbeat-old / recover）
# 前端：cd frontend && AP_BACKEND=http://127.0.0.1:8860 npm run dev（5174）
```

## 6. 声明（如实）

- 演练脚本与日志全部在 `backend/data/e2e-drill/`（**gitignored**）：真实 CLI 与
  模型额度不可作 CI 依赖——报告命令可复跑，脚本不入库（这是记录缺口，不是已完成项）。
- **截图不可用**：browser-use in-app browser surface `visibilityState=hidden`，
  两次尝试报 `NATIVE_BROWSER_VIEWPORT_UNAVAILABLE`；以结构快照 + 网络/console
  记录代留档。
- review 的"判断"由操作面给出（verdict=approve），**审阅判断非模型做出**——身份
  与 API 路径真实，判断来源如实标注。
- 消耗：~12 次真实模型调用经用户 relay（个别失败重试另计）。opencode 配置为
  本卡获授权新增（最小新增，未改其它）。relay 脚本含明文密钥（读取时已入会话
  记录；未复制到任何文件）。
- 本卡附带的最小平台修复：F2（worktree.py + 测试，变异验证）；前端新增线程事件
  时间列（含测试与四件复跑）。其余发现**未修**，按 F 编号移交后续卡。
