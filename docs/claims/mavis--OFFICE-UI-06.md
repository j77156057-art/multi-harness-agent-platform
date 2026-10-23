# 认领：OFFICE-UI-06

| 字段 | 值 |
|---|---|
| task_id | `OFFICE-UI-06` |
| Harness | Mavis |
| 认领时间 | 2026-10-23 20:52 +08:00 |
| 分支 | `agent/local/mavis/OFFICE-UI-02/a1` |
| 状态 | doing |

目标：**讲故事的「今天」** —— 顶上给一段人话，讲这个项目**今天**发生了什么：
「今天做完了 3 件事、卡住 1 件，2 个窗口在干活」，而不是让人自己从一堆数字里读。

为什么：现在头顶给的是**计数徽章**（等人接 1 / 正在做 0 / 等人处理 0 / 没做成 13 /
完成了 4）—— 那是**总共**，不是**今天**，而且是五个数字，得自己加。人的问题是
"今天这个项目怎么样了"，答案应该是一句话。

范围：

- `office/src/flat-office.js` + `office/src/flat-office.css`：顶部那段「今天」；
- `office/src/wording.js`（如需要）：文案去机器味；
- `office/scripts/verify-office-*.mjs`：跟着改的断言（按意图改，不放松判据）。

不碰 `backend/app/api/routes.py`（opencode 在途）、不碰 `backend/app/tasks/**`
（codex 在途）、不碰 `docs/spec/**`、不改平台接口、不改状态机、不改事件名。

先做的事：摸清"今天"这件事**平台侧有哪些事实**（事件的 created_at、任务的
updated_at、额度的用量），哪些能算、哪些算不了 —— 算不了的不写进那句话。
