### MVP-02：SQLite schema 与事件事实源（✅ 已验收）

~~~text
任务编号：MVP-02
负责人 Harness：Codex
认领时间：2026-10-05 05:30
依赖任务：MVP-01（✅ 已完成）
真相源引用：R10 §8（canonical schema）、§15（事件事实源）、
            §23（WAL / migration / 备份）、§11（claim_requests 幂等）
允许修改目录：backend/app/db/** backend/app/events/** backend/migrations/**
禁止修改目录：backend/app/spec.py、docs/spec/**、tools/docstate/**
交付分支：agent/local/codex/MVP-02/a1
commit：b154f5c（8 文件，938 行）
test_id：DOC-SCHEMA-001、CORE-RECOVER-001
测试命令：./scripts.sh check
验收时间：2026-10-05 06:05（WorkBuddy）
当前状态：✅ 已验收

### 验收结果

| # | 检查项 | 结果 | 证据 |
|---|---|---|---|
| 1 | `tasks` 含 attempt_no / hotfix / budget_override_by | ✅ | 001_initial.sql:47-49，NOT NULL + DEFAULT + CHECK |
| 2 | `claim_requests` 有 `unique(claim_request_id, task_id)` | ⚠️ 误判 | 实际由 `task_claims` 满足，见下|
| 3 | `verification_receipts` append-only + 唯一键 | ✅ | `UNIQUE (task_id, attempt_id, "commit", verify_profile)` |
| 4 | WAL + busy_timeout 生效 | ✅ | connection.py:37,39 两个 PRAGMA |
| 5 | migrations 可重复执行 | ✅ | 版本化 runner + `IF NOT EXISTS` + 变更检测 |
| 6 | 门禁三道全绿 | ✅ | docstate 86/86、ruff clean、pytest 20 passed |

### 其他说明（不构成缺陷）

- `seq` 取自 `cursor.lastrowid`：当前 schema 未声明 `WITHOUT ROWID`，rowid 即自增
  主键，成立。但需在 MVP-08 引入多进程写入后重新验证 R10 §13 的「seq 全局单调」。
- 列名用 `commit`（`verification_receipts`）在 SQLite 中合法，且与 R10 文档一致。

已知问题：

- ✅ 无阻塞缺陷。原判定错误已撤回（见上）
- ⚠️ **仓库拓扑分叉**：main 不含 MVP-02 代码 `b154f5c`，需合入
- ⚠️ 仓库尚无 remote，未推送
~~~
