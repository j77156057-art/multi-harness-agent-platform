### MVP-11：集成测试与安全审查（✅ done）

~~~text
任务编号：MVP-11
负责人 Harness：Qoder
开始时间：2026-10-06 09:02
依赖任务：MVP-04~08、MVP-10a、MVP-10b 均已合入 main 并验收。
          MVP-09 按清单自身「依赖现实」条款整体后置（原文：可整体后置到 MVP-11
          之后，先用一个 CLI 脚本代替人工操作 —— 验证目标是协调语义，不是界面）。
          表列依赖保持与清单机械一致（台账书写，不改）；开工范围以本条为准：
          MVP-UI-001 不在本次终审的机械门内。
工作区：.worktrees/qoder/
真相源引用：R10 §10（23 态与集成健康状态机；非终态恢复出口）、§16（verify/receipt）、
            §17（secret 扫描先于落库；`secret_scan_blocked` 在 §37.7 白名单内）、
            §21（Review/Merge Gate）、§25 阶段 5 退出条件、§26（CORE-STATE-COVERAGE-001）、
            §28（不变量）、§29（终审要求与输出格式）、清单 MVP-11 条款
允许修改目录：backend/app/**、backend/tests/**、docs/traceability.md、
            docs/reports/**（新增终审报告目录）、TASKS.md（本卡与任务行）
禁止修改目录：docs/spec/0.3-R10.md、docs/spec/mvp-task-checklist.md、
            tools/docstate/**、tools/discipline/**、tools/mergeguard/**、
            backend/migrations/**、docs/claims/ 下非本任务的文件
交付分支：agent/local/qoder/MVP-11/a1
完成标准：
  1. Secret 扫描先于落库（R10 §17）：对 API 接收的 completion / 消息 / 事件正文做出口
     扫描；命中 → 拒绝落库 + 只写 `secret_scan_blocked`（位置、规则、值 hash，绝不含
     明文）。"先于落库"有顺序性机械用例（命中时目标表零新增行）。
  2. CORE-STATE-COVERAGE-001：非终态恢复出口的集成级覆盖 —— 状态盘点表 +
     每条恢复出口至少一个集成用例；集成健康状态机的四个中间态
     （merge_failure_detected / reverting / retesting / escalated）逐态盘点：
     可实现者补测，超范围者显式记录（不许静默跳过）。
  3. 清单 MVP-11 九类交付逐类有归属：并发 Claim、重复 Claim、Lease 过期恢复、
     旧 epoch 拒绝、Verify/Review/Merge 顺序、直接 push 主分支拒绝、secret 扫描
     先于事件落库、Windows 路径/长路径/worktree 清理测试 —— 已有者指认用例，
     缺者补齐。
  4. 安全审查：10a 卡点名的 `capabilities()` 身份形态、redacted 立场、单账户边界
     （ADR-DEV-002）；10b 验收遗留 R2（Windows 进程树杀灭）修复 + R3（心跳被拒
     路径）补测；结论进终审报告。
  5. 终审报告（docs/reports/）：包含机械校验脚本输出（docstate/discipline/ruff/
     pytest/新套件），不写只有人工核对的结论；所有失败项带 task_id、attempt_id、
     event seq 与复现步骤。
  6. PYTHONUTF8=1 ./scripts.sh check 全绿。
test_id：CORE-STATE-COVERAGE-001（汇总 MVP-04~10 既有 test_id 证据链）
测试命令：PYTHONUTF8=1 ./scripts.sh check
当前状态：done（作者完成，待合入；外部验收待认）
完成时间：2026-10-06 10:01
commit：9a5527c（secret 扫描）、99175b1（状态覆盖+无主 release 修复）、318fa64（进程树+长路径）
验证结果：
  docstate    : 90 项检查，0 项失败
  discipline  : 42 项检查，0 项失败
  ruff        : All checks passed，77 files already formatted
  pytest      : 241 passed（净增 17：安全 6 + 覆盖 6 + 进程树/心跳被拒/长路径 5），exit 0
  终审报告     : docs/reports/mvp-final-report.md（含机械输出、九类交付归属表、21 态覆盖表、
                 安全审查 10 项、健康状态机盘点、§29.2 格式问题清单）
  关键发现     : ① 无主状态 release 合法但运行期崩溃（P1，已修：owner 空 → "unknown" 身份）；
                 ② 深根+长任务名触发 Windows MAX_PATH（P1，已修：canonical 与 worktree 启用
                 core.longpaths）；③ TRAE 验收 R2 进程树杀灭（已修）、R3 心跳被拒路径（已补测）；
                 ④ secret 扫描三入口先于落库 + merge 闸门 fail-closed（全新）
已知问题：
  - **MVP-09 状态更新**：Claude Code 已以「操作员 CLI」形态完成并合入 main（`407e4d0`，
    清单允许的替代）；图形界面不在本次机械门内（报告 §0）。
  - **7 态豁免**（报告 §3.2）：5 态无出口机制（计划修订/依赖巡检/资源巡检/reopen/人类裁决）、
    2 态不可达（drift 探测器未实现）；反向守卫保证实现即红。
  - **特性缺口（记录非静默）**：revert 下游反向闭包（MERGE-REVERT-002）、§17 后半段
    （redact+加密归档）、artifact/mention 扫描面、独立 reverting 健康态——均见报告 §6。
  - **门禁环境**：证据命令均带 PYTHONUTF8=1（P3-3，详见报告 §1.3）。
~~~
