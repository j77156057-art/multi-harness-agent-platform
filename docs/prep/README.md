# prep 共享区

被阻塞的 AI 在这里做"可提前做的准备工作"。

规则：

- 每个任务一个子目录：`docs/prep/<task-id>/`
- **不进主分支** —— prep 产出可能基于错误假设，合入会污染他人
- 完成后在TASKS.md 的"可提前准备工作"表里标记
- prep 产出的内容在对应任务真正开工时**必须重新验证**，不能直接当结论用

## 在你自己的工作区里做

被阻塞的 AI 同样要遵守 H-7 工作区隔离 —— **不要在仓库根目录 `new agent/` 编辑
文件**，否则会因多个 AI 共享工作区而丢提交（本项目已发生 4 次）。

~~~powershell
./scripts/new-ai.ps1 -Harness <你的名字>     # 幂等，已存在则拒绝且不改动
cd .worktrees/<你的名字>
git switch -c agent/<group-short>/<harness>/<task-id>/a1 main
~~~

prep 的产出放你**自己工作区里的** `docs/prep/<task-id>/`，同样不合入主分支；
需要共享时由协调者统一取。规则见 `docs/dev-convention.md` H-7，入口见 `AGENTS.md`。