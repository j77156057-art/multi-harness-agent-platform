# 认领：PUBLIC-RELEASE-01

| 字段 | 值 |
|---|---|
| task_id | `PUBLIC-RELEASE-01` |
| Harness | Mavis |
| 认领时间 | 2026-10-26 09:50 +08:00 |
| 分支 | `agent/local/mavis/PUBLIC-RELEASE-01/a1` |
| 状态 | doing |

目标：将项目整理成适合 GitHub 和简历展示的公开项目，并验证新电脑上的可运行路径。

范围：
- `README.md`：面试官导向的项目说明、安装、启动、验证、目录和边界。
- `backend/requirements*.txt`、`office/package.json`：依赖可复现、前后端安装说明一致。
- `start.bat`、必要的跨平台脚本：按仓库相对位置启动，不依赖本机绝对路径。
- 发布前的路径硬编码扫描与空白环境验证记录。

约束：
1. 不修改 `backend/app/**`、`backend/migrations/**`、`docs/spec/**`。
2. 不修改其他 Harness 的工作区或分支。
3. 先完成本文件和 `TASKS.md` 登记，再修改实现文件。
4. 当前网络若无法访问 PyPI/npm，记录为环境阻塞，不伪造安装成功。
