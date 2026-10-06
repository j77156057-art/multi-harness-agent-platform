"""pytest 全局 fixture。

关于慢测试可见性
----------------
2026-10-06 实测：`tests/test_verify.py` 的 17 个用例跑763 秒。每个用例都从
零建一条完整链条（裸仓库 + worktree + commit + publish，再为 baseline 和
head 各建一个 overlay 做 `git fetch`）。单条 `git fetch` 在本机要 4.45s，
比其它 git 命令（0.4-1.1s）慢一个数量级。

慢的问题是**没人知道**。`pyproject.toml` 里 `slow` / `integration` 两个marker
声明了却零使用，`addopts` 也没有 `--durations`，于是慢测试只有写它的人心里
有数，其他 AI 只能等门禁超时才发现问题出在哪。

本文件提供两件事：
1. 每次运行都打印最慢的若干个测试（`SLOW_TOP_N` 环境变量可覆盖，默认 15）；
2. 超过阈值的测试在汇总里被点名，并提示怎么单独跑它们。

阈值可覆盖的原因：机器负载不同，同一个 30s 的测试在空闲时和在 6 个AI 并发时
含义完全不同。固定阈值在这种机器上要么漏报要么全报，等于没有。
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = BACKEND_ROOT.parent

# 让 `import app` 与 `import docstate` 都能工作
for p in (str(BACKEND_ROOT), str(PROJECT_ROOT / "tools")):
    if p not in sys.path:
        sys.path.insert(0, p)


#: 超过这个秒数就进「慢」名单。可用环境变量覆盖。
DEFAULT_SLOW_SECONDS = 10.0
#: 汇总里最多点名几个。可用环境变量覆盖。
DEFAULT_SLOW_TOP_N = 15


def _env_float(name: str, fallback: float) -> float:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return fallback
    try:
        value = float(raw)
    except ValueError:
        print(f"[conftest] 忽略 {name}={raw!r}（不是数字）", file=sys.stderr)
        return fallback
    return value if value > 0 else fallback


def _env_int(name: str, fallback: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return fallback
    try:
        value = int(raw)
    except ValueError:
        print(f"[conftest] 忽略 {name}={raw!r}（不是整数）", file=sys.stderr)
        return fallback
    return value if value > 0 else fallback


@pytest.fixture(scope="session")
def project_root() -> Path:
    return PROJECT_ROOT


@pytest.fixture(scope="session")
def spec_path(project_root: Path) -> Path:
    path = project_root / "docs" / "spec" / "0.3-R10.md"
    if not path.exists():
        pytest.skip(f"规范文件不存在: {path}")
    return path


# --------------------------------------------------------------------------
# 慢测试可见性
# --------------------------------------------------------------------------

#: nodeid -> 耗时（秒），按完成顺序累加。
_TEST_SECONDS: dict[str, float] = {}
#: nodeid -> 文件名，用于汇总里缩短显示。
_TEST_FILE: dict[str, str] = {}


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_protocol(item: pytest.Item, nextitem):
    started = time.perf_counter()
    try:
        yield
    finally:
        # teardown 也要算进去：这里的慢有相当一部分在 fixture 清理里，
        # 只计 call 阶段会低估。
        _TEST_SECONDS[item.nodeid] = time.perf_counter() - started
        _TEST_FILE[item.nodeid] = str(item.fspath).replace("\\", "/").rsplit("/", 1)[-1]


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    """在 pytest 自己的汇总之后，点名慢测试。

    刻意不重复 pytest 的 durations 表格 —— 那个按耗时排序但不知道多慢算慢；
    这里给出「这个测试超过 N 秒」这个判断，以及怎么单独跑它。
    """
    threshold = _env_float("SLOW_TEST_SECONDS", DEFAULT_SLOW_SECONDS)
    top_n = _env_int("SLOW_TEST_TOP_N", DEFAULT_SLOW_TOP_N)

    slow = sorted(
        ((nodeid, secs) for nodeid, secs in _TEST_SECONDS.items() if secs >= threshold),
        key=lambda pair: pair[1],
        reverse=True,
    )
    if not slow:
        return

    total_slow = sum(secs for _, secs in slow)
    lines = [
        "",
        f"[conftest] 慢测试：{len(slow)} 个超过 {threshold:g}s，"
        f"合计 {total_slow:.1f}s"
        f"（{total_slow / max(1.0, sum(_TEST_SECONDS.values())) * 100:.0f}% 的测试时间）",
    ]
    for nodeid, secs in slow[:top_n]:
        lines.append(f"  {secs:7.2f}s  {nodeid}")
    if len(slow) > top_n:
        lines.append(f"  ...另有 {len(slow) - top_n} 个未列出（设 SLOW_TEST_TOP_N 看全部）")

    files = sorted({_TEST_FILE.get(nodeid, "?") for nodeid, _ in slow})
    lines.append(f"  涉及文件：{', '.join(files)}")
    lines.append(f'  单独跑最慢的一个：pytest "{slow[0][0]}" -q')
    lines.append("  跳过全部慢测试：  pytest -m 'not slow'")
    lines.append("  调阈值：          SLOW_TEST_SECONDS=30 pytest")
    print("\n".join(lines), file=sys.stderr)
