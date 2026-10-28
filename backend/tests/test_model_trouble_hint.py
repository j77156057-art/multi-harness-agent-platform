"""「模型用不了」要能被认出来，别的失败不能被认成它。

这条判据决定办公室会不会弹窗。判宽了，人会被「输出不是 JSON」「平台拒收」
这类与模型无关的失败反复打扰，从此学会无视弹窗；判窄了，真正卡住的窗口没人管。
所以这里两头都钉：该命中的必须命中，不该命中的一个字都不能带。
"""

from __future__ import annotations

import pytest

from app.adapter.formal_worker import _provider_outage_hint
from app.adapter.harnesses.cli import CliFailed, CliRunResult


def _failure(stderr: str) -> CliFailed:
    return CliFailed(
        "FD-0000000000000001",
        CliRunResult(
            argv=["codex", "exec"],
            returncode=1,
            stdout="",
            stderr=stderr,
            duration_seconds=1.0,
        ),
    )


@pytest.mark.parametrize(
    "stderr",
    [
        # 这台机器上真实发生过的那一条：本地中转没在跑。
        "ERROR stream disconnected before completion: error sending request for url"
        " (http://127.0.0.1:8402/responses)",
        "Error 429: rate limit exceeded for this minute",
        "insufficient balance: quota exhausted",
        "401 unauthorized: invalid api key",
        "Error: connect ECONNREFUSED 127.0.0.1:8402",
    ],
)
def test_a_model_that_cannot_be_used_is_named(stderr):
    """限流、额度用尽、连不上、认证被拒 —— 换一个模型就能继续。"""
    hint = _provider_outage_hint(_failure(stderr))

    assert hint.startswith("（模型用不了：")
    # 带的是 CLI 自己说的那一句，不是转述：人要看出是哪个通道、什么问题。
    assert stderr.splitlines()[0][:40] in hint


@pytest.mark.parametrize(
    "stderr",
    [
        # 平台拒收：产物越界，换个模型也不解决。
        "Refused: path outside exclusive_paths",
        # 模型答了，只是答得不好。
        "the model output carried no balanced JSON object",
        # 平台腾不出工位。
        "WorktreeCapacityExceeded: no free worktree slot right now",
    ],
)
def test_other_failures_are_not_called_a_model_problem(stderr):
    """这些换个模型也不解决，混进弹窗只会让人以后无视它。"""
    assert _provider_outage_hint(_failure(stderr)) == ""
