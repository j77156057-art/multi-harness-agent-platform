# README-REVAMP-01

### 统一任务卡

任务编号：README-REVAMP-01
负责人 Harness：Mavis
开始时间：2026-10-26 11:32 +08:00
工作区：`.worktrees/readme`
依赖任务：—
真相源引用：`backend/app/`、`backend/tests/`、`office/`、`README.md`
当前状态：done
完成时间：2026-10-26 11:48 +08:00
commit：README 实现待提交；登记提交 `accef44`
验证结果：docstate 98/98、discipline 44/44、README 路径扫描和 `git diff --check` 通过；前端 `npm ci` / `npm run build` 采用既有 PUBLIC-RELEASE-01 验证记录，未因本次文档改动复跑。
交付分支：`agent/local/mavis/README-REVAMP-01/a1`

test_id：`MVP-PROCESS-001`
测试命令：`python tools/docstate/docstate.py`；`python tools/discipline/discipline.py`；README 路径扫描；前端构建命令核对
允许修改目录：`README.md`、`TASKS.md`、`docs/claims/mavis--README-REVAMP-01.md`、`docs/tasks/notes/README-REVAMP-01.md`
禁止修改目录：`backend/app/**`、`backend/migrations/**`、`docs/spec/**`、其他 Harness 工作区与分支

## 目标

将 README 从安装清单升级为面向面试官的项目案例说明，使读者在几分钟内理解：

1. 项目解决的真实协作问题与使用场景。
2. Claim/Lease/Fencing、Verify/Review/Merge、事件溯源和 Harness 适配器分别解决什么问题。
3. 为什么采用 FastAPI + SQLite + Agent Office，以及哪些能力依赖外部平台。
4. 如何在新电脑上运行、观察、验证，并区分本地演示与真实联调。
5. 哪些结论有测试、门禁或真实运行证据，哪些仍是明确边界。

## 完成标准

- `README.md` 首屏包含项目定位、核心场景和可展示入口。
- README 包含问题/方案、架构、关键机制、运行方式、验证证据和边界说明。
- README 不出现本机绝对路径、凭据、令牌或未经代码支持的能力宣称。
- `docstate` 与 `discipline` 门禁通过。
