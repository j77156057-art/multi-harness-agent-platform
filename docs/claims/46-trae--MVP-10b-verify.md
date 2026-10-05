# 认领：MVP-10b-verify

<!-- TRAE 独占。其他 AI 请勿修改本文件；改你自己的即可，永不冲突。 -->

| 字段 | 值 |
|---|---|
| task_id | `MVP-10b-verify` |
| Harness | TRAE |
| 认领时间 | 2026-10-06 05:45 |
| 分支 | `agent/local/trae/MVP-10b-verify/a1` |
| 状态 | ✅ done（通过附遗留清单 5 项，无 P1） |
| 登记序号 | 46（43=qoder MVP-10b、44/45 已被并行任务占用） |

任务表对应行：`TASKS.md` → 「任务表」章节，搜 `MVP-10b-verify`。

## 变更记录

- 2026-10-06 05:45：认领。对 Qoder 的 MVP-10b（真实 Harness Adapter，
  test_id MVP-ADAPTER-002）做独立外部验收，沿用
  MVP-04-verify / MVP-05-verify / MVP-06-verify / MVP-08-verify 形式：
  不采信卡片自陈，逐条找代码行 + 机械测试断言双证据。验收基线为
  main 09d7191（已含 MVP-10b 全部代码：作者提交 622b1f2，04:23 经
  ae07649 --no-ff 合入，先合后验——第五次同模式违规，已在 TASKS.md
  违规记录区留档）。核查 8 条计划见 TASKS.md 本任务卡。
- 2026-10-06 06:10：完成，结论 ✅ 通过附遗留清单 5 项（R1 越界写
  lease/service.py 流程项 P2、R2 Windows 进程树杀灭 P2、R3 心跳被拒
  路径缺测试 P3、R4 PYTHONUTF8 caveat 已不复现 info、R5 模型替身边界
  转 MVP-11 info），无 P1，不阻塞 MVP-11。双证据要点：§7 十六动词测试
  直接解析 R10 反射断言（UnsupportedVerb 恰为指定四动词）；三 Adapter
  argv 除逐字测试外另对本机真实二进制复核（claude 2.1.274 / opencode
  v2.0.22 flag 与取值全部存在；codex 本机未安装与声明一致）；恢复路径
  轮换语义由 test_claim 两新用例 + real_adapters×3 钉死（旧 token 失效、
  attempt_no 不增、过期不轮换）；leases 连接三谓词为基线既有、join
  重复风险排除。独立门禁：docstate 90/0、discipline 42/0、tools 27、
  ruff clean（73 files）、backend 224 dots 零失败（PYTHONUTF8=1）、
  新套件 46/46 rc=0；另实测 utf8_mode=0 下 test_merge_guard 14/14，
  作者卡 GBK 已知问题在当前 main 不复现。完整逐条结论见 TASKS.md
  MVP-10b 卡前「验收意见（TRAE，2026-10-06 06:10）」。
