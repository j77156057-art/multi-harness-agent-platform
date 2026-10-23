# 认领：assistant-react-loop-p3

| 字段 | 值 |
|---|---|
| task_id | `assistant-react-loop-p3` |
| Harness | Codex |
| 认领时间 | 2026-10-23 18:36 +08:00 |
| 分支 | `agent/local/codex/OFFICE-UI-01/a1` |
| 状态 | doing（审核助攻实现＋scripted 测试已就位，本轮提交；验收守门另立提案 review-contract-gate 待复核） |

## 做什么（P3 第一刀：审核助攻）

1. `task.review_pack` 只读工具：验收条款逐条 + diff 读数 + 验证回执 +
   `check_kinds` 机械计数，一次取证；不产出裁决。
2. prompt 审核协议：对照只述事实（挂不上写"未记上"）、裁决挂"待审核人"、
   human 类条款不做机械假证；自检清单加第 7 项。
3. 引擎机械门零放宽：say 仍是唯一写，引用照旧可回查。

## 边界

- 不改 `backend/app/tasks/service.py`（守门提案落点，共享在途）与
  `backend/app/admin/**`；不新增表/事件/路由/权限键；
- 守门半件按共享文件纪律单开提案 `docs/proposals/review-contract-gate.md`
  （waiting），未写代码。

## 完成标准

认领与卡里 rehearsed 的六条 scripted（三读齐/计数/补丁透传/诚实透传/
标识符校验/loop 全链＋prompt 边界）＋ ruff、docstate、discipline 坚持、
pytest 定点绿、与全量测试零新增失败。
