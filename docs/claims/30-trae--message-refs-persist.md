# 认领：message-refs-persist

<!-- TRAE 独占。其他 AI 请勿修改本文件；改你自己的即可，永不冲突。 -->

| 字段 | 值 |
|---|---|
| task_id | `message-refs-persist` |
| Harness | TRAE |
| 认领时间 | 2026-10-05 22:21 |
| 分支 | `agent/local/trae/message-refs-persist/a1` |
| 状态 | ✅ done |
| 登记序号 | 30（原拟 29，与已合 main 的 29-qoder--MVP-05.md 撞号，顺延） |

任务表对应行：`TASKS.md` → 「任务表」章节，搜 `message-refs-persist`；
任务卡（含完整证据与门禁数字）：`TASKS.md` 搜同名三级标题。

## 变更记录

- 2026-10-05 22:21：认领。修复 MVP-08 验收遗留 3b：post_message 接收
  refs 且回显，但 task_messages 无该列、从不落库，GET 读不回。
  openapi（post_message 请求体与 Message schema）本就声明 refs，
  属实现回归契约；R10/prep 无需改。
- 2026-10-05 22:30：完成。迁移 004 为 task_messages 增加
  refs TEXT NOT NULL DEFAULT '[]'（JSON TEXT），service 校验后同事务
  落库、读取解码为数组，存量行回填 []。backend pytest 116 项退出 0，
  docstate 90/0、discipline 39/0、tools pytest 14、ruff 全过、
  prep check_contract FAILURES 0、mutation 10/10。实现提交 cf2b46c，
  本行为随附回填提交。MVP-08 验收卡遗留 3b、message-global-seq 卡
  与 traceability 的开放标注均已同步关闭。
