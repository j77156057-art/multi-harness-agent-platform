# 认领：gate-summary-fix

<!-- OpenCode 独占。其他 AI 请勿修改本文件；改你自己的即可，永不冲突。 -->

| 字段 | 值 |
|---|---|
| task_id | `gate-summary-fix` |
| Harness | OpenCode |
| 认领时间 | 2026-10-09 01:30 |
| 分支 | `agent/local/opencode/gate-summary-fix/a1` |
| 状态 | 🟢 doing |

任务表对应行：`TASKS.md` → 「任务表」章节，搜 `gate-summary-fix`。

## 修什么

`tools/mergeguard/cli.py` 的 `_run_gate()` 用**最后一行**当门禁摘要：

```python
lines = [ln for ln in (proc.stdout + proc.stderr).splitlines() if ln.strip()]
...
tail = lines[-1] if lines else "(无输出)"
return proc.returncode == 0, f"{name}: {tail}"
```

门禁在「结果」之后**完全可能**再打印东西：Python 3.13 对无效转义序列的
`SyntaxWarning`、`DeprecationWarning`、`--json` 切换时的提示。实测吃过：
discipline 打印的 SyntaxWarning 落在结果之后，摘要变成那条警告，**真正的检查
结果被吃掉**，而退出码仍是 0 —— 门禁是绿的，绿灯的证据是错的。

## 修法

认 `结果:` 这一行（docstate 与 discipline 都用它收尾：`结果: N 项检查，N 项失败`，
可带 `，N 项告警`）。找不到摘要行时才退到最后一行，并**明确标注**
「未找到摘要行」—— 两种情况的门禁自述强度不一样，不能都当成「门禁说了什么」。

用「包含」而非「以…开头」匹配：测试里的门禁替身打印的是
`打印"{name}: 结果: ..."`，行首有 gate 名。生产里两门禁都是裸的 `结果:`。

## 为什么这是个值得单独修的问题

`_run_gate` 是 mergeguard 判断「门禁说了什么」的唯一来源。它显示给用户看的那行
文字，是**绿灯的证据**。证据本身可以门禁改成任何样子而不被发现，等于把
「合并是否安全」的判断建立在一行不可信的输出上。

## 已知不在本次范围

`_run_gate` 只跑 docstate + discipline 两道门禁；ruff 与 pytest 在
`scripts.sh check` 里，不在 mergeguard 的合并路径上。那是另一个问题。

## 变更记录

- 2026-10-09：认领。