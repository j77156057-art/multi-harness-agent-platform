### e2e-harness-drill：Codex＋OpenCode 真实联调 + 故障演练 + 前端追溯（🚧 doing）

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
测试命令：PYTHONUTF8=1 ./scripts.sh check（演练本体为报告内可复跑命令）
当前状态：doing
~~~
