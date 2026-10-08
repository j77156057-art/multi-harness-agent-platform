# 认领：landing-page

<!-- 本文件由 Qoder 创建。其他 AI 请勿修改本文件；改你自己的即可，永不冲突。 -->

| 字段 | 值 |
|---|---|
| task_id | `landing-page` |
| Harness | Qoder |
| 认领时间 | 2026-10-08 13:08 |
| 分支 | `agent/local/qoder/landing-page/a1` |
| 状态 | ✅ done（实现 a6237dc；待合入） |

任务表对应行：`TASKS.md` → 「任务表」章节，搜 `landing-page`。

## 变更记录

- 2026-10-08 13:08：认领（协调者「加」——确认根路径落地页）。协调者实启后端后
  浏览器请 `/` 得 404（无根路由，属预期但体验差）：本卡加 `GET /` 落地页
  （列出 /health、/docs、/spec/status、/api/v1 前缀与前端 5173 提示）+ `GET
  /favicon.ico` 返回 204（消除噪音 404）。Host 校验中间件对根路径同样生效。
- 2026-10-08 13:22：实现完成并提交 `a6237dc`（app.py 两路由 + test_landing.py
  4 断言 + README 验证表一行）。全量门禁 455 passed 绿；实机 curl（8870 临时
  实例）三项留档；登记锚点曾误挂未合并的 runtime-driver 行，已修正为
  start-bat 行（`0adc4ff`）。状态 → done。
