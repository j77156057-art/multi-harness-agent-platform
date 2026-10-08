# 认领：start-bat

<!-- 本文件由 Qoder 创建。其他 AI 请勿修改本文件；改你自己的即可，永不冲突。 -->

| 字段 | 值 |
|---|---|
| task_id | `start-bat` |
| Harness | Qoder |
| 认领时间 | 2026-10-08 12:48 |
| 分支 | `agent/local/qoder/start-bat/a1` |
| 状态 | ✅ done（实现 5520786；待合入，外部验收待认） |

任务表对应行：`TASKS.md` → 「任务表」章节，搜 `start-bat`。

## 变更记录

- 2026-10-08 12:48：认领（协调者「帮我加一个 start.bat 文件」）。Windows 一键
  启动器：默认起后端（8848）；`all` = 后端+巡检+前端三窗口；`workers` /
  `frontend` 单起；`help` 用法。纯 ASCII 文案（cmd 代码页安全）、
  `PYTHONUTF8=1`/`PYTHONIOENCODING=utf-8`、venv/依赖缺失给修复提示。
  实测：help 模式 + 默认模式起真服务（/health 200，AP_DATA_DIR 走临时目录
  不碰共享数据）；`all`/`frontend` 会弹窗不自动跑，如实声明。

- 2026-10-08 13:02：完成。实现 `5520786`；全量门禁绿（pytest 451 不变）。
  实测：help exit 0；默认模式 /health 200（临时数据目录）；workers 模式 14 行
  巡检 JSON；all/frontend 未自动弹窗（如实声明）。README 快速开始已补入口。
