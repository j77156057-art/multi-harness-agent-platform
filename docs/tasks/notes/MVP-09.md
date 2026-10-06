### MVP-09：最小前端 —— CLI 形态（done）

~~~text
任务编号：MVP-09
负责人 Harness：Claude Code
开始时间：2026-10-06 09:03
工作区：.worktrees/claude/
依赖任务：MVP-08（✅ 已验收）
真相源引用：清单 MVP-09（含「先用一个 CLI 脚本代替人工操作」的出口）；
            R10 §7（接口面）；docs/dev-convention.md H-1/H-5
允许修改目录：backend/app/cli/**、backend/tests/**、docs/traceability.md、TASKS.md、
            docs/claims/**
禁止修改目录：backend/app/api/**（MVP-08 已验收）、backend/app/**（除 cli/）、
            docs/spec/0.3-R10.md（只读）
交付分支：agent/local/claude/MVP-09/a1
commit：407e4d0
test_id：MVP-UI-001
当前状态：done

### 形态决策：CLI 而非界面

清单对 MVP-09 写明可以「**先用一个 CLI 脚本代替人工操作** —— 因为 MVP 的验证目标
是协调语义，不是界面」，而完成标准「人类不直接访问数据库即可完成一条完整任务链路」
不要求浏览器。两个支撑事实：

- **CI 完全没有前端门禁**（`gate.yml` 无 npm/node 步骤）。一个无门禁、无测试的
  Vue UI 按 `traceability.md` 自己的标准会落进「仅是名字」那一类，无法被验证
- CLI 可用 pytest 直接驱动，是可机械验证的

### 交付

`backend/app/cli/`（`main.py` + `__main__.py` + `__init__.py`）：一条只经 HTTP 的
操作员命令行，覆盖 建群 / 发方案 / 批准 / 看板 / 任务详情 / 事件 / 群聊 /
领取 / 续租 / 提交 / 发布 / 求助 / 放弃 / 审查 领取与提交。

`backend/tests/test_cli_flow.py`（6 项）。

### 完成标准的两半如何被断言

| 完成标准 | 断言方式 |
|---|---|
| 人类不直接访问数据库即可完成一条完整任务链路 | 链路测试全程走 CLI，**断言里不出现任何 SQL** |
| **前端不能绕过 API 改权威字段** | **源码级断言**：CLI 不得 import `app.db` 或任何 service。绕过 API 的写会同时绕过 fencing 与 `task_events`，让「投影可重建」失真 —— 所以这条不是风格偏好 |

第二条用源码级断言而非「跑一遍看看」，因为直接调 service 往往看起来更省事，
而它只会在很久以后以难查的方式出问题。

### 认领前做了全仓扫描（采纳 dup-incident 事故的补救建议）

~~~text
git branch -a | grep MVP-09 / frontend   → 无
git log --all --grep="MVP-09"            → 3 条，全是文档提及，无实现
git log --all -- frontend/               → 仅 810d88d（MVP-01 骨架）
~~~

**这是那条补救建议的第一次实际使用**，确认了 MVP-09 确实无人开工。

### 验证结果

  docstate    : 90 项检查，0 项失败
  discipline  : 42 项检查，0 项失败
  ruff check  : All checks passed!
  ruff format : 77 files already formatted
  pytest      : 全量 **exit 0**；新增 `test_cli_flow.py` 6 项全过
  真实入口    : `python -m app.cli --help` 子进程跑通（证明不只是函数可调）

### 查出的两处真实缺口（已写进 docs/traceability.md）

MVP-09 是第一个真把整条链路走完的交付，因此**撞出了两个不在 §7 里、但操作链路
离不开的步骤**：

1. **`claimed → in_progress`（`start_task`）**：§7 根本没有这个 verb（只有推送模型
   的 `start()`，标为「未来才需要」），而 `submit_result` 要求任务已在 `in_progress`
2. **产出 verify receipt**：Verify Worker 没有端点

**后果**：操作员或任何 harness **无法只经 API 走完「领取 → 提交 → 审查」**。
`test_cli_flow.py` 与既有的 `test_api_flow.py` 都是直接调
`SchedulerService.start_task` / `VerifyWorker.run` 绕过，并在用例注释里**显式标注**
—— 不伪造 receipt、不开旁路。

要真正闭合需在 §7 增补 verb（走 R10 复审），或明确「这两步不属 adapter 职责」——
后者目前是对的，但**没有任何规范文字这么说过**，所以每个实现者都要重新发现一遍。

### 过程中我写错的三处（都由平台或测试纠正）

1. **响应形态猜错**：以为 `GET /groups/{g}/tasks` 返回数组、`POST messages` 返回
   `body` —— 实际前者是 `{"tasks": [...]}`、后者只回 `message_id/seq/mentions/refs`。
   写测试前**先探了真实形态**才发现（`probe_cli.py`），否则会写出断言错形状的测试
2. **以为链路到 submit 就够**：被 `review.requires_verify_receipt` 拒回 ——
   review 需要 `awaiting_review`，那需要 verify receipt。是平台的 guard 纠正了我
3. **Windows 子进程编码**：`subprocess.run(text=True)` 默认按 GBK 解，而 `--help`
   有中文，抛 `UnicodeDecodeError` 并把 stdout 变成 None —— 表现是崩溃而非断言失败。
   必须显式 `encoding="utf-8"`

已知问题：
- 链路上两步无 HTTP 通路（见上），本任务记录而不自补端点 —— 新增端点属 MVP-07/08
  范围，两者均已验收（H-5）
- `projects` / `groups` 只有 POST 没有 GET，CLI 因此无法「列出」项目与群组
- Merge Gate 完全没有 HTTP 路由（MVP-07 交付了 `app/merge/**` 但未暴露），
  所以清单要的「Merge Gate 页面」以 CLI 形态也无法驱动
- 目标是 CLI，不是图形界面；Vue 骨架（MVP-01 建的 7 个文件）未动
~~~
