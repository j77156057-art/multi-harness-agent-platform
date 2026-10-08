# 认领：auth-layer

<!-- 本文件由 Qoder 创建。其他 AI 请勿修改本文件；改你自己的即可，永不冲突。 -->

| 字段 | 值 |
|---|---|
| task_id | `auth-layer` |
| Harness | Qoder |
| 认领时间 | 2026-10-08 15:35 |
| 分支 | `agent/local/qoder/auth-layer/a1` |
| 状态 | 🚧 doing |

任务表对应行：`TASKS.md` → 「任务表」章节，搜 `auth-layer`。

## 变更记录

- 2026-10-08 15:35：认领（协调者路线图第 2 项主体："Harness token、主体身份、
  403、权限矩阵执行……解决某个 AI 冒领、越权修改、身份混淆"）。切片取
  permission-matrix 报告的 A+B：主体令牌 + API 层矩阵执行（C=OS ACL 与
  D=协作者/secret 面显式后置并如实挂缺）。前置 `R10-fix-12`（✅ 1866fad）已把
  `permission_denied` 登记进 §37.7 O 集合——本卡是它的第一个生产者。
  v1 边界：`AP_AUTH_MODE=off`（默认，零行为变化）/`enforce`（矩阵生效）；
  前端 token 接线与三账户 ACL 不在本卡，enforce 推广前必须先做前端，如实披露。
