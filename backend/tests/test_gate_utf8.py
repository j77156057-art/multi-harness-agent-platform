"""门禁的 UTF-8 行为：固化在代码里，不依赖手动环境变量。

**回归的起因是一个实测崩溃**：`tools/docstate/docstate.py --verbose` 在 Windows 上
直接 `UnicodeEncodeError: 'gbk' codec can't encode character '⊆'`（U+2286），
exit 1。`gbk` 与 `cp936` 都编不出它。CI 跑的是不带 `--verbose` 的 docstate，
所以从没撞到；开发者一加 `--verbose` 就崩。

第二个问题是输出编码跟着控制台代码页走：此前三种情况下门禁输出**都不是合法
UTF-8**，被日志系统或 CI artifact 收走就是乱码（`gate.yml` 正是重定向收 --json）。
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
DOCSTATE = REPO / "tools" / "docstate" / "docstate.py"
DISCIPLINE = REPO / "tools" / "discipline" / "discipline.py"
MERGEGUARD = REPO / "tools" / "mergeguard" / "cli.py"

GATES = (DOCSTATE, DISCIPLINE, MERGEGUARD)
ENCODINGS = ("gbk", "cp936", None)


def _run(script: Path, *args: str, encoding: str | None) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env.pop("PYTHONIOENCODING", None)
    env.pop("PYTHONUTF8", None)
    if encoding:
        env["PYTHONIOENCODING"] = encoding
    return subprocess.run(
        [sys.executable, str(script), *args],
        env=env,
        cwd=str(REPO),
        capture_output=True,
    )


@pytest.mark.parametrize("script", GATES, ids=lambda p: p.parent.name)
@pytest.mark.parametrize("encoding", ENCODINGS, ids=lambda e: e or "unset")
def test_gate_output_is_valid_utf8_under_any_console_encoding(script, encoding):
    """门禁输出必须**恒为合法 UTF-8**，不跟控制台代码页走。

    这是第二个问题的守卫：以前三种编码下输出都无法按 UTF-8 严格解码，
    于是任何收门禁输出的环节（CI artifact、日志收集）拿到的都是乱码。
    """
    args = ("status",) if script == MERGEGUARD else ()
    proc = _run(script, *args, encoding=encoding)
    proc.stdout.decode("utf-8")  # 严格解码，非 UTF-8 会抛
    proc.stderr.decode("utf-8")
    assert proc.returncode == 0, proc.stderr.decode("utf-8", "replace")[-800:]


@pytest.mark.parametrize("encoding", ENCODINGS, ids=lambda e: e or "unset")
def test_docstate_verbose_does_not_crash(encoding):
    """**回归**：`docstate --verbose` 曾因 `⊆`（U+2286）崩溃（exit 1）。

    这个 bug 之所以长期没被发现：CI 跑的是不带 `--verbose` 的版本，
    而断言消息里恰好只有这一条含不可编码字符。修法是把入口的 stdio 钉成
    UTF-8 + errors="replace"（见各入口的 `_force_utf8_stdio`）。
    """
    proc = _run(DOCSTATE, "--verbose", encoding=encoding)
    err = proc.stderr.decode("utf-8", "replace")
    assert proc.returncode == 0, err[-800:]
    assert "UnicodeEncodeError" not in err, err[-400:]
    # 且确实打印了通过项（否则「不崩」可能只是因为什么都没输出）
    assert "⊆" in proc.stdout.decode(
        "utf-8", "replace"
    ), "这条用例的前提是 --verbose 真的会打印那条含 ⊆ 的消息"


def test_all_three_gates_have_the_hardening():
    """三处都要有，且都要在 `main()` 里**先于任何输出**调用。

    刻意复制 6 行而不是共享一个模块：三个入口都以 `python tools/X/Y.py` 独立运行，
    sys.path[0] 是自己所在目录，跨目录 import 要先改 sys.path —— 为 6 行去动三个
    入口的启动路径不划算。复制的代价是「改一处要改三处」，所以用这条钉住。
    """
    for script in GATES:
        text = script.read_text(encoding="utf-8")
        assert "def _force_utf8_stdio(" in text, f"{script.name} 缺少 _force_utf8_stdio"
        assert (
            'encoding="utf-8", errors="replace"' in text
        ), f"{script.name} 没有同时指定 encoding 与 errors"
        body = text[text.index("def main(") :]
        first = body[body.index("\n") + 1 :].lstrip()
        assert first.startswith("_force_utf8_stdio()"), (
            f"{script.name} 的 main() 第一件事应当是 _force_utf8_stdio()，"
            f"实际第一件事是：{first[:60]}"
        )
