# PUBLIC-RELEASE-01

### 统一任务卡

任务编号：PUBLIC-RELEASE-01
负责人 Harness：Mavis
开始时间：2026-10-26 09:50 +08:00
工作区：`.worktrees/release`
依赖任务：—
真相源引用：`README.md`、`backend/requirements*.txt`、`office/package.json`、`start.bat`、`scripts.sh`、`docs/dev-convention.md` H-7/H-8
当前状态：review
交付分支：`agent/local/mavis/PUBLIC-RELEASE-01/a1`

## 目标

将项目整理成适合 GitHub 和简历展示的公开项目，并验证新电脑上的可运行路径：

1. README 面向面试官说明项目价值、架构、前置条件、Windows/Linux/macOS 安装、启动、验证、目录和已知边界；不依赖本机绝对路径。
2. 依赖文件覆盖后端运行/测试与前端构建，启动入口按仓库相对位置工作；提供一次命令化安装或清晰等价命令。
3. 对受支持运行路径做路径硬编码扫描，并在空白环境中执行安装、后端健康检查、前端构建；失败项如实记录。
4. 现有门禁不回归；发布前核对 GitHub 远端状态与提交树。

test_id：`MVP-PROCESS-001`
测试命令：`./scripts.sh check`；`npm ci && npm run build`；空白环境启动与 HTTP health 检查
允许修改目录：`README.md`、`backend/requirements*.txt`、`office/package.json`、`start.bat`、`start-office.bat`、`scripts.sh`、发布所需的新增检查脚本与配置示例
禁止修改目录：`backend/app/**`、`backend/migrations/**`、`docs/spec/**`、其他 Harness 工作区与分支

## 验证结果

| 检查 | 结果 |
|---|---|
| `python tools/docstate/docstate.py` | 98/98 通过 |
| `python tools/discipline/discipline.py` | 44/44 通过 |
| `bash -n scripts.sh setup.sh start-backend.sh` | 通过；`scripts.sh` 已统一为 LF，可在 Git Bash 解析 |
| PowerShell 解析 `setup.ps1` | 通过 |
| `office/npm ci` | 通过，17 个包，0 vulnerabilities |
| `office/npm run build` | 通过，Vite 生成 `dist/index.html`、`direct.html`、`outputs.html` |
| 后端依赖安装 | 未完成：当前机器访问 `pypi.org` 多次连接超时 |
| 后端 pytest / HTTP health | 未执行：发布工作树虚拟环境因上述网络阻塞未安装 pytest |
| README 与受支持入口的绝对路径扫描 | 未发现本机绝对路径 |

## 已知边界

联网安装可能受当前机器到 PyPI/npm 的网络连通性影响；真实 Harness CLI 不是项目零配置运行前置条件。当前机器未能完成后端依赖安装，因此后端 pytest 与 HTTP health 仍待网络可用后复跑。
