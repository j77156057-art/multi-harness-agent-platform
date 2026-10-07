# 认领：win-utf8-stdio

<!-- OpenCode 独占。其他 AI 请勿修改本文件；改你自己的即可，永不冲突。 -->

| 字段 | 值 |
|---|---|
| task_id | `win-utf8-stdio` |
| Harness | OpenCode |
| 认领时间 | 2026-10-07 03:55 |
| 分支 | `agent/local/opencode/win-utf8-stdio/a1` |
| 状态 | 🟢 doing |

任务表对应行：`TASKS.md` → 「任务表」章节，搜 `win-utf8-stdio`。

## 为什么文件名没有序号

方案 C（`docs/proposals/claim-denum.md`）已裁决并开始落地，`docs/claims/`
的文件名不再以序号开头。本文件按**新约定** `<harness>--<task-id>.md` 命名。

## 要修什么

把「门禁在 Windows 上正确处理 UTF-8」这件事从**手动环境变量**固化到代码里。

### 实测到的两个问题

**问题 1：`tools/docstate/docstate.py --verbose` 直接崩溃（exit 1）**

```
File "tools/docstate/docstate.py", line 426, in main
    print("   ok    " + i["msg"])
UnicodeEncodeError: 'gbk' codec can't encode character '\u2286' in position 22
```

`⊆`（U+2286 SUBSET OF OR EQUAL TO）出现在一条断言消息里，而 **`gbk` 与
`cp936` 都编不出它**。CI 跑的是不带 `--verbose` 的 `docstate`，所以从没撞到；
开发者一旦加 `--verbose` 想看细节就崩。`discipline --verbose` 侥幸没崩，
只是靠那条消息恰好不含不可编码字符。

**问题 2：门禁输出不是合法 UTF-8**

三道门禁在 `PYTHONIOENCODING` 为 `gbk` / `cp936` / 未设三种情况下的输出，
**全部无法按 UTF-8 严格解码**。也就是说输出编码跟着控制台代码页走，
而不是固定的 UTF-8。任何把门禁输出收进日志/CI artifact 的环节拿到的都是
乱码，而 `gate.yml` 正是用重定向收 `--json` 的。

### 修法

三个门禁入口（`docstate` / `discipline` / `mergeguard cli`）在启动时把
`sys.stdout` / `sys.stderr` 重新configure 成 **UTF-8 + errors="replace"**：

- UTF-8：`⊆`、`§`、全角标点、emoji 都能编码，输出恒为合法 UTF-8；
- `errors="replace"`：万一还有别的字符编不出，**降级**而不是抛异常崩掉。

刻意**不再**依赖 `PYTHONIOENCODING` / `PYTHONUTF8` 这类环境变量 ——
门禁是给别人用的工具，要求每个人先设环境变量才能正确跑，本身就是缺陷。

## 变更记录

- 2026-10-07 03:55：认领（文件名按方案 C 的新约定，不带序号）。