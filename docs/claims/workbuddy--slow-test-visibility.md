# 认领：slow-test-visibility

| 字段 | 值 |
|---|---|
| task_id | `slow-test-visibility` |
| Harness | WorkBuddy |
| 认领时间 | 2026-10-06 08:45 |
| 分支 | `agent/workbuddy/slow-test-visibility` |
| 状态 | ✅ done |
| 工作区 | `.worktrees/workbuddy/` |
| 依据 | `test_verify.py` 全量763s 无人在看；慢测试只有作者自己知道 |

任务表对应行：`TASKS.md` → 「任务表」，搜 `slow-test-visibility`。

## 背景

`backend/tests/test_verify.py` 17 个用例跑 **763 秒**，而 pyproject 里
`slow` / `integration` 两个 marker **已声明但零使用**，`addopts` 里也没有
`--durations`。结果是：慢的只有写测试的人知道，其他 AI 只能等门禁超时才发现。

## 要做什么

1. `pyproject.toml` 的 `addopts` 加 `--durations=15`，让每次跑都自带慢测试排行；
2. `conftest.py` 加一个 session 级hook：把「超过阈值的测试」自动标为慢，
   并在终端汇总里点名，避免每个人自己去翻 durations 输出；
3. 给 `test_verify.py` 里的 12 个慢用例补 `@pytest.mark.slow`（让 marker
   真正被用上，也方便用 `-m "not slow"` 快速跑一轮）；
4. 阈值必须可覆盖（环境变量），否则机器慢时会被全部标慢，失去意义。

## 明确不做

- **不改 `app/verify/overlay.py` 去优化 `git fetch`**。实测 fetch 4.45s
  慢一个数量级，但把它优化到 0.1s 全量也只省 14%（753s -> 649s），
  改实现的风险大于收益。
- **不做 session 级模板缓存**。已实测为负收益（647s vs 479/525s），
  见 TASKS.md「chain-template 优化：已做、已测、已否决」。

## 验收

- `pytest -q` 的输出里能看到慢测试排行与点名汇总；
- `pytest -m "not slow"` 能跑出一轮快的；
- 门禁四道全绿。

提交：`9f122cd`

Refs: MVP-PROCESS-001
