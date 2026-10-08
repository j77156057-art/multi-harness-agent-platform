### e2e-harness-drill：Codex＋OpenCode 真实联调 + 故障演练 + 前端追溯（✅ done）

~~~text
任务编号：e2e-harness-drill
负责人 Harness：Qoder
开始时间：2026-10-08 08:16
依赖任务：MVP-10b（真实适配器，✅）、verify/review/merge（✅ MVP-06/07）、
          前端三栏（✅ frontend-thread）、diff-message（✅ 线程内 diff 可顺带验）
工作区：.worktrees/qoder/
真相源引用：R10 §6/§7（16 动词契约=适配器 argv 实测对象）、§10（状态链）、
            §11（Claim 幂等）、§13（Lease/fencing/heartbeat、"续租拒绝=失联"）、
            §16（Verify receipt）、§21（Merge Gate）、§22（前端只读面）
范围声明：
  - **本切片**（证据类交付，三部分）：
    ①**真实全流程**：在**测试项目**（独立 canonical 仓：小 python 项目 + pytest）
      上，codex（桌面包内 bin/codex.exe）与 opencode（v2.0.22）**各以真实 CLI
      完成一个任务**：认领→启动（scheduler 配 worktree）→心跳→真实产出
      commit→publish→submit→Verify（真实 cmd 判据 receipt）→独立 Review
      （异 provider harness 身份经 API 提交；**审阅判断来源如实声明**）→
      Merge Gate（integration 复跑 + merged）。
    ②**故障演练 6 项**：并发抢同一任务（两 harness 同时 claim）、进程退出
      （杀 worker/CLI → 心跳停）、租约过期（小 lease 配置 + 巡检回收）、
      旧 token 写入（旧 epoch/lease 心跳必须被 fencing 拒）、重启恢复
      （杀 uvicorn 再起，claim_request_id 重放→同 attempt+新 token→续跑完成）、
      worktree 隔离（两任务并发跑，各自 worktree/分支/未提交文件互不可见）。
    ③**前端追溯**：vite 指向演练后端，浏览器（结构快照口径）核对：群聊、任务
      线程事件（claim/start/publish/submit/verify_passed/review/merge）、负责人、
      时间戳、审核结果（verdict/findings payload）。
  - **产物**：`docs/reports/e2e-real-harness-drill.md`（可复跑命令 + 原始输出
    摘录 + **发现清单**：平台缺陷/环境观察/后续候选）；演练脚本落
    `backend/data/e2e-drill/`（gitignored——真实 CLI 与额度不可作 CI 依赖，
    命令在报告中完整可复跑，如实声明脚本不入库）。
  - **环境事实（如实）**：codex 不在 PATH（桌面应用包内）；两者模型调用经
    本机 relay（8402，用户设施，仅本机 loopback）；opencode 默认模型区域受限
    ——已获授权为其新增最小 provider 配置指向 relay；relay 含明文密钥（未复制
    入库/报告）。
  - **显式出范围**：真实模型的"审阅判断"（Review 提交走真实 API 路径，但
    verdict 由操作面给出——除非顺带让 reviewer CLI 真读 diff，则如实标注）；
    性能/压测；认证层（不存在，仍旧单账户 loopback）；把演练脚本入共享门禁。
  - **仅允许的最小代码改动**：若演练发现平台缺陷且能用测试钉死 → 本卡内修复
    （含 pytest，门禁全绿）；大型改动另立卡、如实移交。
允许修改目录：docs/reports/**、TASKS.md、docs/traceability.md、
            docs/tasks/notes/e2e-harness-drill.md、
            docs/claims/qoder--e2e-harness-drill.md、backend/data/**（gitignored
            演练区）、backend/**（仅缺陷小修+测试，见上）
禁止修改目录：docs/spec/**、tools/**（不动共享工具链）
交付分支：agent/local/qoder/e2e-harness-drill/a1
完成标准：
  1. 两个真实 CLI 各完成一次完整生命周期（认领→启动→心跳→提交），原始 CLI
     输出/平台事件留档；publish 后 canonical 有各自分支，Merge 后集成分支
     含 merge commit
  2. Verify receipt 为真实 cmd 判据；Review 异 provider 提交（cross_provider
     满足）；Merge Gate 复跑绿 → merged
  3. 故障演练六项逐项留档（场景/注入/平台反应/结论）；旧 token 必须被拒
     （fencing）；重启重放必须拿到同 attempt + 新 lease token 且随后完成
  4. worktree 隔离有双向证据（并发期间两 worktree 的 git status 互不含对方
     未提交文件；分支/commit 独立）
  5. 前端结构快照覆盖：群聊、线程（负责人/时间/审核结果/payload）、diff 卡
     （顺带）；截图不可用则如实声明
  6. `PYTHONUTF8=1 ./scripts.sh check` 全绿（有小修则测试净增）；traceability
     新增 `OPS-DRILL-001`
test_id：OPS-DRILL-001（新登记）
测试命令：PYTHONUTF8=1 ./scripts.sh check（演练本体为报告 §5 可复跑命令）
当前状态：done（作者完成，待合入；外部验收待认）
完成时间：2026-10-08 11:24
commit：`0c6b40e`（实现提交；合并提交见 git log main）
验证结果：
  docstate    : 90 项检查，0 项失败
  discipline  : 44 项检查，0 项失败
  ruff        : All checks passed（118 files already formatted）
  pytest      : 443 passed（alternates 修复含字节断言+变异验证；计数不变），exit 0
  前端四件     : vue-tsc 0 错；vitest 27 passed（含新增事件时间戳断言）；
                build 110.30 kB；contrast 9 对全 ≥4.5:1
  真实全流程   : **6 条链 merged**（T-CDX2/codex、T-OC/opencode、T-ISO-A/codex、
                T-ISO-B/opencode、T-RESTART2/opencode、T-RESTART3/codex〔重放
                补跑闭环〕）；另有 T-CDX 被平台正确拦下（空产出→
                verify_code_failed→changes_requested）。事件链实例：claim→start→
                submit_result→verify_passed→approve(异 provider)→merge_start→
                merge_success；集成分支按序累计 merge。
  故障演练六项 : ①并发抢=opencode claimed / codex already_owned（恰一胜者）；
                ②进程退出=CLI 运行中 taskkill，心跳停；③租约过期=真实巡检器
                `-m app.scheduler --once`（首轮 actions=3→stale，二轮同钟 0，
                幂等）；④旧 token=409 `fencing.epoch_stale`（对照新 token 200）；
                ⑤重启恢复=杀 uvicorn→worker ConnectError 失联退出→重启→同
                claim_request_id 重放挂回**同 attempt/worktree**并补跑完成
                （4 轮，前 3 轮完成段被 relay 抖动阻断，第 4 轮闭环）；
                ⑥worktree 隔离=并发双任务 distinct paths/branches+交叉文件
                互不可见（双向断言）。
  前端追溯     : 群聊（operator+时间）、线程全链事件（**新增时间列**：claim 01:15:17
                →merge_success 01:18:10）、负责人（codex/opencode/verify-worker/
                merge-worker）、审核 payload（review_id/reviewed_commit/findings_hash）、
                diff 卡（4 文件 +13−5；.pyc 标"二进制"；空产出诚实显示"0 文件"）、
                任务列表异常态（changes_requested/stale/abandoned）；当前页
                console 零消息、网络 7/7 全 200。
  附修复       : **worktree alternates CRLF**（git 不剥尾 CR→worktree 每条 git 命令
                报 `object directory ... does not exist`；显式 `newline="
"` +
                字节断言，变异验证去掉即红）；前端线程事件时间列（追溯要求）。
已知问题（发现清单 F1-F13，详见报告）：
  - **F1（高）**：`claim_for_revision`/`resubmit`（R10 L449/523）未实现——verify
    失败后重领被 already_owned 永久挡住，**返工是死端**；建议优先立卡。
  - F3/F4（高，适配器）：codex workspace-write 沙箱在本机起不了 shell
    （CreateProcessAsUserW）+ 引号经 cmd 被拆；opencode 由 python 直启不稳
    （DB 错/usage dump），bash 壳稳定——适配器/驱动面硬化另立卡。
  - F5：review_policy=cross_provider 无强制；F6：verify/merge 无生产驱动进程；
    F7：worktree 无回收→容量闸 8/8 挡新任务；F8：_tick 只映射 AdapterError；
    F9：replay 响应不含 worktree 路径；F10：codex usage 未接线（预算数据源）；
    F11：靶场判据需 delta 语义（空产出可通过存在性判据）；F12/F13：本机文件锁
    高发 + relay 高抖动（环境，非平台）。
  - **脚本 gitignored**（`backend/data/e2e-drill/`）：真实 CLI/额度不可作 CI
    依赖，报告命令可复跑；截图不可用（浏览器 surface hidden）以结构快照留档。
~~~
