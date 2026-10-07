# 认领：alert-webhook

<!-- 本文件由 Qoder 创建。其他 AI 请勿修改本文件；改你自己的即可，永不冲突。 -->

| 字段 | 值 |
|---|---|
| task_id | `alert-webhook` |
| Harness | Qoder |
| 认领时间 | 2026-10-08 02:13 |
| 分支 | `agent/local/qoder/alert-webhook/a1` |
| 状态 | 🟢 doing |

任务表对应行：`TASKS.md` → 「任务表」章节，搜 `alert-webhook`。

## 变更记录

- 2026-10-08 02:13：认领。协调者方向：「不用真搬源码，通过源码学到需要的内容，
  转换成我们自己的东西」。吸收清单第一件 = **出站告警 webhook**（机制学习来自
  Paperclip 的出站 webhook 模式，见 `docs/reports/paperclip-buzz-source-review.md`
  §2.8；**原生实现、零代码复制**——不需要归属框架）。目标：关闭 jsonl-export
  卡里记录的「无外部告警通道」缺口：任务进入 `escalated`（含全部升级边，边集合
  从冻结迁移表推导）→ HMAC 签名 POST 到配置的 webhook → 失败重试、最终停靠可观察。
