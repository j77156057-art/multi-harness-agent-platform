### 违规记录：fix-flaky-init 撤回登记时缺任务卡（9d3d3ef / e656058）

**事实**：WorkBuddy 的 fix-flaky-init 在认领表（10:06）与任务表均标为 done
（结论：63 次复现无法复现、撤回 flaky 登记，交付提交 `9d3d3ef`，
随 MVP-04 合并 `e656058` 入 main），但**同样没有任务卡**。
自 `e656058` 起 discipline `check_cards` 又回到 1 项失败：
「已认领且非在途的任务都有任务卡；缺卡=['fix-flaky-init']」。
这是继 R10-fix-10 之后**同类缺卡第二次发生**。

**已采取的纠正**：TRAE 以独立任务 fix-flaky-init-card
（`agent/local/trae/fix-flaky-init-card/a1`）登记先行、依 `9d3d3ef`
与本文件 flaky 撤回小节补录任务卡，未改动调查结论；补卡后 discipline 恢复 0 失败。

**根因**：与 R10-fix-10 相同——关闭动作只改状态不补卡；且该提交搭乘
MVP-04 合并进入 main，合并时无人核对待合入分支上 done 任务的卡片完整性。
**已连续两次同类违规：建议把「合并前检查分支内所有标 done 任务均有完整卡」
并入合并者检查清单，不能只依赖事后 discipline 红灯补救。**

### fix-flaky-init：并发初始化 flaky 定向复现（✅ done，结论：无法复现，撤回登记；任务卡事后补录）

~~~text
任务编号：fix-flaky-init
负责人 Harness：WorkBuddy
开始时间：2026-10-05 10:06
完成时间：2026-10-05 10:17（9d3d3ef）
依赖任务：MVP-02（并发初始化与 WAL 锁修复 7531592 的后续排查）
真相源引用：本文件「⚠️ 更正：flaky 未能复现，撤回登记（10:10）」小节
            （63 次复现的完整数据表与误判根因）、提交 9d3d3ef
允许修改目录：TASKS.md（纯调查任务，无代码改动）
交付分支：agent/workbuddy/fix-flaky-init（实际提交 9d3d3ef 随 MVP-04 合并 e656058 入 main）
背景：09:55 在「已知 flaky」中登记
      test_concurrent_initialization_and_events_have_one_order 偶发失败（1/9），
      并猜测了句柄释放/Defender 两个原因，但未抓到 ^E 错误行。
完成内容：
  1. 定向复现 63 次：单测连跑 20/20、全量连跑 15/15、
     复刻原场景 16/16、模拟门禁三道 12/12，全部通过；
  2. 定位原「1/9」为判读错误：pytest -q 成功只输出进度行无 N passed，
     把另一轮的 FAILED 行误归因到该轮；
  3. 撤回 09:55 的 flaky 登记，不掩盖误报。
test_id：CORE-RECOVER-001
测试命令：cd backend; python -m pytest -q（复现统计以退出码判定）
当前状态：done（结论：无法复现，撤回登记）
commit：9d3d3ef（仅 TASKS.md，经 e656058 合入 main）
验证结果：
  - 63 次定向复现 0 失败（数据表见本文件撤回小节与 9d3d3ef）；
  - 合入后 main 全量 pytest 85 passed（e656058 基线，TRAE 复验）。
补录说明：
  - 本卡由 TRAE 于 2026-10-05 19:25 依 9d3d3ef 与本文件撤回小节事后补录：
    WorkBuddy 完成撤回登记时遗漏任务卡，导致 discipline check_cards
    自 e656058 起 1 项失败，见本文件违规记录区。
~~~
