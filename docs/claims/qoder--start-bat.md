# 认领：start-bat

<!-- 本文件由 Qoder 创建。其他 AI 请勿修改本文件；改你自己的即可，永不冲突。 -->

| 字段 | 值 |
|---|---|
| task_id | `start-bat` |
| Harness | Qoder |
| 认领时间 | 2026-10-08 12:48 |
| 分支 | `agent/local/qoder/start-bat/a1` |
| 状态 | 🚧 doing |

任务表对应行：`TASKS.md` → 「任务表」章节，搜 `start-bat`。

## 变更记录

- 2026-10-08 12:48：认领（协调者「帮我加一个 start.bat 文件」）。Windows 一键
  启动器：默认起后端（8848）；`all` = 后端+巡检+前端三窗口；`workers` /
  `frontend` 单起；`help` 用法。纯 ASCII 文案（cmd 代码页安全）、
  `PYTHONUTF8=1`/`PYTHONIOENCODING=utf-8`、venv/依赖缺失给修复提示。
  实测：help 模式 + 默认模式起真服务（/health 200，AP_DATA_DIR 走临时目录
  不碰共享数据）；`all`/`frontend` 会弹窗不自动跑，如实声明。
