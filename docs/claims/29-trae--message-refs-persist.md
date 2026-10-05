# 认领：message-refs-persist

<!-- TRAE 独占。其他 AI 请勿修改本文件；改你自己的即可，永不冲突。 -->

| 字段 | 值 |
|---|---|
| task_id | `message-refs-persist` |
| Harness | TRAE |
| 认领时间 | 2026-10-05 22:21 |
| 分支 | `agent/local/trae/message-refs-persist/a1` |
| 状态 | 🟢 doing |
| 登记序号 | 29 |

任务表对应行：`TASKS.md` → 「任务表」章节，搜 `message-refs-persist`。

## 变更记录

- 2026-10-05 22:21：认领。修复 MVP-08 验收遗留 3b：post_message 接收
  refs 且回显，但 task_messages 无该列、从不落库，GET 读不回。
  openapi（post_message 请求体与 Message schema）本就声明 refs，
  属实现回归契约；R10/prep 无需改。
