# 认领：lint-baseline-repair

<!-- OpenCode 独占。其他 AI 请勿修改本文件；改你自己的即可，永不冲突。 -->

| 字段 | 值 |
|---|---|
| task_id | `lint-baseline-repair` |
| Harness | OpenCode |
| 认领时间 | 2026-10-09 01:40 |
| 分支 | `agent/local/opencode/lint-baseline-repair/a1` |
| 状态 | 🟢 doing |

任务表对应行：`TASKS.md` → 「任务表」章节，搜 `lint-baseline-repair`。

## 修什么

从**仓库根**跑 ruff（全量口径）有 2 条既有问题。我此前一直只在 `backend/`
跑，所以报了一路「ruff 全绿」——那是单目录口径，不是仓库口径。

```
docs/prep/MVP-08/mutation_test.py:130  F541  f-string without any placeholders
tools/docstate/docstate.py:176         E741  ambiguous variable name `O`
```

`git stash` 掉我全部改动后同样报这 2 条 → **既有问题，不是任何在途任务引入的**。
已在 main 上挂了两天没人认领，因为两条都不在任何人的任务卡允许目录内
（`tools/docstate/**` 在多张任务卡里明令禁止修改）。**本次由用户明确授权扩目录。**

## 修法

**F541**（`docs/prep/MVP-08/mutation_test.py:130`）

    print(f"baseline (unmutated contract):")
    → print("baseline (unmutated contract):")

纯删 `f` 前缀，字符串无占位符，**输出逐字节不变**。

**E741**（`tools/docstate/docstate.py:176`）

    M, O, R = al.get("M", set()), al.get("O", set()), al.get("R", set())

只改**局部变量名** `O` → `ops`。字典键 `"O"`、断言消息文案里的 `"O"`
（"O 计数 = %d"）**一律不动** —— `M`/`O`/`R` 是 R10 §37.7 allowlist 的三类
领域术语，是文档事实不是代码风格。字典里 `EXPECTED_COUNTS = {"M": 74,
"O": 14, ...}` 同理保持原样。

只改 `O` 不改 `M`/`R`：ruff 的 E741 只针对 `l`/`O`/`I` 这三个视觉上与数字
混淆的字母，`M`/`R` 不触发。为对称把三个都改名会扩大 diff 面积，
而动 `EXPECTED_COUNTS` 的键有改变断言语义的风险——不值得。

## 风险与验证

`tools/docstate/docstate.py` 是门禁，改它必须自证「没把门禁改坏」：

1. 改前先记录 docstate 的完整基线输出（90 项检查，0 项失败）；
2. 改后逐字节比对输出，必须完全一致；
3. `backend/tests/test_gate_utf8.py`（13 条）会跑真实 docstate，顺带兜住
   「门禁还能不能跑、输出是否仍为合法 UTF-8」。

## 变更记录

- 2026-10-09 01:40：认领（用户授权扩目录）。