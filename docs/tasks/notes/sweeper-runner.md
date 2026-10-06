### sweeper-runner：巡检器运行器（✅ done）

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
当前状态：done（作者完成，待合入；外部验收待认）
完成时间：2026-10-07 02:0x（跨零点，登记日 2026-10-06）
commit：（本卡所在提交）
验证结果：
  docstate    : 90 项检查，0 项失败
  discipline  : 46 项检查，0 失败（1 条 rootworktree 警告为他人根目录占用的旁证，非本任务）
  ruff        : All checks passed
  pytest      : 280 passed（本任务净增 7 项：test_sweeper_runner.py），exit 0
  关键断言     : runner 真实驱动 lease 巡检（过期租约→stale，事件恰一条）；顺序列表钉死
                 （插入中间必须有意为之）；同钟二跑 no-op（不重复转移）；sweep_progress
                 迁移可用；入口三例（无库拒绝=2、--once 输出 JSON 行、--loop 在
                 AP_SWEEPER_ENABLED=0 时拒绝=2）
已知问题：
  - 实施修订已在提案 §3 记录：不再并入操作员 CLI（MVP-09 的 HTTP-only 不变量），
    改独立入口 `python -m app.scheduler`
  - lifespan 后台形态未做（设计的第 3 形态），留运维接线
  - 后续巡检（deps-resources / aging-stuck / workers / drift）按设计 §6 逐个认领，
    每加一个都要同步更新 SWEEPS 顺序钉死测试与状态覆盖豁免表
~~~
