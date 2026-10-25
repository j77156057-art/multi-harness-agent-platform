# 认领：OFFICE-UI-07

| 字段 | 值 |
|---|---|
| task_id | `OFFICE-UI-07` |
| Harness | Mavis |
| 认领时间 | 2026-10-23 21:20 +08:00 |
| 分支 | `agent/local/mavis/OFFICE-UI-02/a1` |
| 状态 | doing |

目标：任务详情里的「这件事要做什么」**说人话** —— 现在那里摆的是一整段机器 JSON
（真数据里是一份 CLI 报错转储），人读不懂；改成人能读的话，原文收在后面。

为什么：这段文本是**机器写给机器的**（CLI 把它自己的报错转储塞进了工单）。人看
它，得先在几百行 JSON 里找"到底哪儿错了"。平台上已经有翻译服务（规则层 + 管理员
自己的模型通道，带缓存），这一处就是它的下一 applications。

范围：

- `office/src/flat-office.js` + `office/src/flat-office.css`：这一块怎么显示；
- `office/src/wording.js`：调用平台 summarize（问过的走缓存，不重复问）；
- `office/scripts/verify-office-*.mjs`：跟着改的断言（按意图改，不放松判据）。

不碰 `backend/app/api/routes.py`（opencode 在途）、不碰 `backend/app/tasks/**`
（codex 在途）、不碰 `backend/app/pool/**`、不碰 `docs/spec/**`、不改平台接口、
不改状态机、不改事件名。翻译服务（`backend/app/wording/**`）若要动，只动规则层
的确定性部分，**不加新的模型调用入口**。

先做的事：先看清那段 JSON 到底是什么、来自哪个字段、哪些任务的 goal 是人话（那就
别乱翻），再决定怎么显示。
