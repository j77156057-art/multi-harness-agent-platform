# README-TONE-01

### 统一任务卡

任务编号：README-TONE-01
负责人 Harness：Mavis
开始时间：2026-10-26 12:35 +08:00
工作区：`.worktrees/readme-tone`
依赖任务：README-REVAMP-01
真相源引用：`README.md`、`backend/app/`、`backend/tests/`
当前状态：done
完成时间：2026-10-26 12:36 +08:00
commit：README wording correction pending commit; registration commit `f51b26a`
验证结果：README 元话术扫描无匹配；docstate 98/98、discipline 44/44 与 `git diff --check` 通过。
交付分支：`agent/local/mavis/README-TONE-01/a1`

test_id：`MVP-PROCESS-001`
测试命令：README 元话术扫描；`python tools/docstate/docstate.py`；`python tools/discipline/discipline.py`；`git diff --check`
允许修改目录：`README.md`、`TASKS.md`、本任务 claim 与任务卡
禁止修改目录：`backend/app/**`、`backend/migrations/**`、`docs/spec/**`、其他 Harness 工作区与分支

## 完成标准

- README 以正常项目定位和能力说明开篇，不出现简历、面试官或面试追问自我说明。
- 保留项目问题、架构、关键机制、运行方式、验证和边界等实质内容。
- 文案克制、专业，不使用面向读者的求职/面试指令。
- 任务登记与文档门禁通过。
