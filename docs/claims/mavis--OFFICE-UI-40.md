# 认领：OFFICE-UI-40

| 字段 | 值 |
|---|---|
| task_id | `OFFICE-UI-40` |
| Harness | Mavis |
| 认领时间 | 2026-10-26 06:45 +08:00 |
| 分支 | `agent/local/mavis/OFFICE-UI-40/a1` |
| 状态 | doing |

目标：**接手 codex 的在途**，结掉 `pool-metered-amount`（计量档的 token 与余额）。

## 接手时的实况

实现与定点测试**已在 main 上**（`backend/app/pool/balance.py` 是余额读数，
`service.py` 有 `_usage_blobs()` 三协议 usage 解析与 `tokens_unknown`）。
卡上写「doing（实现与定点测试已就位；待 Merge Gate）」。

另外：那张卡的文件本身是 **GBK 编码**（在 UTF-8 的仓库里显示为乱码），这一张顺手
转成 UTF-8（OFFICE-UI-27 清理过仓库里的坏字符，编码也要一样）。

## 范围

- `docs/tasks/notes/pool-metered-amount.md`（GBK → UTF-8；状态、验证结果）
- `docs/claims/codex--pool-metered-amount.md`（补记核实结论）
- `TASKS.md`
- `docs/claims/mavis--OFFICE-UI-40.md`、`docs/tasks/notes/OFFICE-UI-40.md`

## 怎么做到不出错

1. **不碰实现**（代码在 main 上且绿）；核实出缺口就另开卡。
2. 转码要**先按 GBK 读出原文再按 UTF-8 写回**，不能直接改字节（那是「写坏中文」）。
3. 逐条核实它自己写的 6 条完成标准。

## 完成标准

1. 6 条完成标准逐条核实 + 定点测试真跑（真起本地 HTTP 服务发流式请求）。
2. 那张卡转成 UTF-8，状态改成 done。
3. 门禁全绿。
