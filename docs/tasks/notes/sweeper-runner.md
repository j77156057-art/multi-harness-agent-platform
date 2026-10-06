### sweeper-runner：巡检器运行器（🚧 doing）

~~~text
任务编号：sweeper-runner
负责人 Harness：Qoder
开始时间：2026-10-06 12:10
依赖任务：sweeper-family（✅ 设计已交付并经协调者采纳，2026-10-06）
工作区：.worktrees/qoder/
真相源引用：docs/proposals/sweeper-family.md §3（运行器架构）与 §4（参数清单）；
            R10 §13（既有 lease 巡检为原型）；R10 §10（巡检顺序约定）
允许修改目录：backend/app/scheduler/**（新增）、backend/app/config.py、
            backend/migrations/**（仅新增 007_sweep_progress.sql）、backend/tests/**、
            docs/proposals/sweeper-family.md（仅追加实施修订记录）
禁止修改目录：backend/app/claim/**、backend/app/lease/**、backend/app/verify/**、
            backend/app/merge/**、backend/app/api/**、backend/app/cli/**、
            docs/spec/**、tools/**、docs/claims/ 下非本任务的文件
交付分支：agent/local/qoder/sweeper-runner/a1
完成标准：
  1. `app/scheduler/`：SweepReport + SWEEPS 固定序注册表 + run_all_sweeps(database, now=)；
     既有 lease 巡检（expire_stale_leases）接入为第 1 个成员（§7.1 顺序约定）
  2. `007_sweep_progress.sql`：卡住指纹的载体表（sweeper-family §2.5），本任务只建表
  3. 参数集中：设计 §4 的全部新增参数进 Settings（默认值按设计），环境变量可覆盖
  4. 独立入口 `python -m app.scheduler [--once|--loop]`——**实施修订**：设计 §3 建议
     并入操作员 CLI，但 MVP-09 CLI 有硬不变量「只经 HTTP、不 import db/service」
     （test_cli_flow 钉死），巡检必须同进程触库；改为独立模块入口，修订记录追加到
     提案 §3
  5. 测试：runner 跑通真实 lease 巡检（注入 now）、顺序钉死、二次运行 no-op、
     迁移冒烟、`--once` 入口端到端（临时 AP_DATA_DIR）
test_id：SCHED-SWEEP-001
测试命令：PYTHONUTF8=1 ./scripts.sh check
当前状态：doing
~~~
