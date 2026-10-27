# 一次真实闭环的截图证据

这两张图来自**真实正式平台**的一次完整协作闭环，不是演示数据：

1. `office-project-office.png` —— Agent Office 里「多 Harness 协作」项目的办公室。
   顶栏写着「已连接平台 · 窗口 13 在线」，统计是「今天新接手 5 条任务、完成 1 条、未完成 4 条。
   额度池今天用了 50 次」；任务列表里那条 **已完成** 的就是本轮交付的
   「作品页交付 · 交付 portfolio/index.html 单页作品说明」（第 1 次尝试，最多 3 次）。
   右侧两个折叠面板也是这一轮的真实状态：
   「Harness CLI · 下面是 6 个 Harness CLI 的官方安装方式；这台机器还没探测。」
   与「额度池 · 20 个额度来源，已用 81 次」（展开后能看到
   `harness/codex-window-admin × 25 · codebuddy:minimax-m3.1-flash × 25`
   —— 管理员这一轮的全部模型调用都走了平台自己的额度池）。

2. `delivered-index-html.png` —— 合入之后、**直接用 file:// 打开**的作品页
   （`portfolio/index.html`，自包含静态页，无外部请求、无脚本）。

## 这一轮真实发生的事

| 步骤 | 记录 |
| --- | --- |
| 群主提需求 | 群消息 `project-goal`，管理员先回问细节（`needs_owner`），群主补充真实测试路径 |
| 管理员细化 | `plan-bb7552909a014774`，草案快照 `snap-82496036383e4d3dbe76d6f93339f2f4` |
| 第 1 轮传阅 | 6 个成员窗口各交一份意见（claude-code / codebuddy-member-1 / opencode / pi / qoder-member-1 / qoder-member-2） |
| 管理员汇总并改版 | 快照 `snap-73fb0ab2d3724e42966d29fd0e9cb1b8` |
| 第 2 轮传阅 | 同一批 6 个成员各审**新方案**一遍 |
| 管理员定稿 | 快照 `snap-55210ac5bca9414080b8508b9c74c755` |
| 群主批准 | `plan.approve` |
| 分配轮 | `alloc-816100c0421944989f8a704db93502e5`（开 → 成员申请 → 管理员裁决 → 本人接受） |
| 执行 | `codebuddy-member-1` 领取 attempt `FD-f8909e100ea7433f.a1`，真 CLI 写出 `portfolio/index.html` |
| 验证 | `verify_passed`，回执 `rc-8631a5a6e67d469c8ad391405b78c68d` |
| 跨厂商审阅 | `codex-window-admin`（openai）审 `codebuddy-member-1`（tencent）的产出 → `approve` |
| 合入 | merge commit `db834070ed3103c644620f6f85c7871edcf5b851` |

审阅意见逐条核对了验收契约（每条意见都带 `contract_id`）：9 个测试文件路径、
12 个用例名、3 条启动命令都在仓库里真实存在，页面里零外链、零脚本。
