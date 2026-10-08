### start-bat：Windows 一键启动器（✅ done）

~~~text
任务编号：start-bat
负责人 Harness：Qoder
开始时间：2026-10-08 12:48
依赖任务：无（协调者直接指令；README 快速开始的 Windows 封装）
工作区：.worktrees/qoder/
真相源引用：README「快速开始」（启动命令的事实源）；Makefile `dev`/`frontend-dev`
            目标与 `./scripts.sh dev`（等价命令已在仓库内，本卡只是 Windows
            cmd 原生封装——不新增任何启动语义）
范围声明：
  - **本切片**：仓库根 `start.bat`（纯 ASCII 文案——cmd 按活动代码页解析 .bat，
    中文会在 chcp 生效前乱码，故不用中文）+ README 快速开始补一行入口。
    模式：
      `start.bat`            → 后端 `python -m app.main`（8848，前台）
      `start.bat all`        → 三窗口：后端 / 巡检 `app.scheduler --loop` /
                               前端 `npm run dev`
      `start.bat workers`    → 巡检循环（前台）
      `start.bat frontend`   → 前端 dev server（前台）
      `start.bat help`       → 用法
    约定：`cd /d %~dp0`（放根或副本都能跑）；`PYTHONUTF8=1` +
    `PYTHONIOENCODING=utf-8`；venv 缺失 / 前端 node_modules 缺失 → 打印修复
    命令并 exit 2；数据目录沿用默认（测试时用临时 AP_DATA_DIR，不碰共享数据）。
  - **显式出范围**（记录不静默）：不开机自启/服务化（无 Windows 服务）；不装
    依赖（venv/npm install 仍手工一次）；不覆盖 `alerts/liveness/budget/
    exporter` 的 `--loop`（help 里列命令，但不预置窗口——避免四个黑窗）；
    harness worker 侧（联调脚本）不在本卡。
  - **零迁移、零后端代码**。
允许修改目录：start.bat（新，仓库根）、README.md（快速开始一行）、TASKS.md、
            docs/tasks/notes/start-bat.md、docs/claims/qoder--start-bat.md
禁止修改目录：backend/**、frontend/**、docs/spec/**、tools/**
交付分支：agent/local/qoder/start-bat/a1
完成标准：
  1. `start.bat help` → 打印用法，exit 0（实测命令+输出留档）
  2. 默认模式**起真服务**：临时 AP_DATA_DIR 下 `cmd /c start.bat` →
     `GET /health` 200 → 停止（实测留档；不碰仓库共享 data/）
  3. venv 缺失路径的提示文案正确（代码审阅；不实际移走 venv 测试——如实声明）
  4. `all` / `frontend` 模式不自动弹窗实测（会打扰操作者桌面）——命令内容
     逐行为本会话已验证命令（`app.scheduler --loop` / `npm run dev`），
     卡内如实声明此边界
  5. `PYTHONUTF8=1 ./scripts.sh check` 全绿（本卡无代码，pytest 数不变）；
     README 入口行落地
test_id：MVP-PROCESS-002（traceability 已有行；本卡为启动器封装）
测试命令：PYTHONUTF8=1 ./scripts.sh check（+ 卡内 start.bat 实测记录）
当前状态：done（作者完成，待合入；外部验收待认）
完成时间：2026-10-08 13:02
commit：`5520786`（实现提交；合并提交见 git log main）
验证结果：
  docstate    : 90 项检查，0 项失败
  discipline  : 44 项检查，0 项失败
  ruff        : All checks passed（119 files already formatted）
  pytest      : 451 passed（本卡无代码，计数不变），exit 0
  start.bat 实测:
    ① `cmd /c start.bat help` → 用法输出，**exit 0**；
    ② **默认模式真起服务**：临时 AP_DATA_DIR 下 `cmd /c start.bat` →
       uvicorn 起于 8848 → `GET /health` **200**（config.data_dir 确认为
       临时目录，未碰共享 data/）→ 停止并清理；
    ③ **workers 模式**：`PYTHONUNBUFFERED=1` 下同法起 `app.scheduler --loop`
       → **14 行巡检 JSON**（expired_leases 等全家族一轮）→ 停止。
  自查       : 按命令行精确识别并清理本卡启动的全部进程（未误杀 main 门禁的
               pytest / 他人进程）；临时数据目录已删。
已知问题：
  - `all` / `frontend` 模式**未自动弹窗实测**（会打开三个真实窗口打扰操作者；
    如实声明）：其命令内容逐行为本会话已验证命令（`app.main` /
    `app.scheduler --loop` / `npm run dev`），start 语法经审阅。
  - venv 缺失 / node_modules 缺失的提示分支仅代码审阅（不实际移走依赖实测）。
  - 不预置 `alerts/liveness/budget/exporter` 的 `--loop` 窗口（help 里列出
    命令）——避免四个黑窗；需要者自行开。
  - start.bat **不在共享门禁覆盖内**（门禁不跑 bat；与前端组件测试同类的
    记录缺口，如实声明）。
~~~
