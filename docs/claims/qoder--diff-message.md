# 认领：diff-message

<!-- 本文件由 Qoder 创建。其他 AI 请勿修改本文件；改你自己的即可，永不冲突。 -->

| 字段 | 值 |
|---|---|
| task_id | `diff-message` |
| Harness | Qoder |
| 认领时间 | 2026-10-08 04:48 |
| 分支 | `agent/local/qoder/diff-message/a1` |
| 状态 | 🚧 doing |

任务表对应行：`TASKS.md` → 「任务表」章节，搜 `diff-message`。

## 变更记录

- 2026-10-08 04:48：认领。吸收队列 ⑤（协调者「先推 ⑤」）。机制学习：Buzz
  kind:40008 diff/patch 消息——`buzz-sdk/src/builders.rs` 的
  `build_diff_message`（结构化元数据 tags：repo 必填 http(s)、commit 必填
  ≥7 hex、parent-commit、branch=(src,tgt)、file、pr、l、description、
  truncated、alt；正文=patch 且 60KB 上限）+ `buzz-relay/src/handlers/
  ingest.rs` 的 `validate_diff_event`（同口径服务端校验）——本会话已逐行核读，
  代码原生重写。原生对位见任务卡（我们的线程=事件投影，diff 不落库、按需从
  canonical 仓计算；结构化元数据映射见卡内映射表）。
