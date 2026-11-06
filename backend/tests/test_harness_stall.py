"""静默挂死检测：三项同时不动才判，慢但仍在产出的运行不得被误杀。"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from app.adapter.harnesses.cli import HarnessStalled, SubprocessRunner
from app.adapter.stall import ProgressSample, StallDetector
from app.config import settings


def test_all_three_signals_flat_for_the_window_is_stalled():
    detector = StallDetector(window_seconds=10)
    sample = ProgressSample(bytes_total=100, cpu_seconds=1.0, tokens=(1, 50))
    assert detector.observe(sample, now=0) is False
    assert detector.observe(sample, now=5) is False
    assert detector.observe(sample, now=10) is True
    assert detector.stalled_seconds(now=10) == 10


@pytest.mark.parametrize(
    "changed",
    [
        ProgressSample(bytes_total=101, cpu_seconds=1.0, tokens=(1, 50)),
        ProgressSample(bytes_total=100, cpu_seconds=1.5, tokens=(1, 50)),
        ProgressSample(bytes_total=100, cpu_seconds=1.0, tokens=(1, 80)),
    ],
    ids=["output-bytes", "cpu-time", "output-tokens"],
)
def test_any_single_signal_growing_resets_the_window(changed: ProgressSample):
    detector = StallDetector(window_seconds=10)
    detector.observe(ProgressSample(bytes_total=100, cpu_seconds=1.0, tokens=(1, 50)), now=0)
    assert detector.observe(changed, now=30) is False
    assert detector.observe(changed, now=31) is False


def test_unknown_output_tokens_never_triggers():
    """拿不到用量时宁可漏判：只有两个信号不能构成"三项同时不动"。"""
    detector = StallDetector(window_seconds=10)
    sample = ProgressSample(bytes_total=100, cpu_seconds=1.0, tokens=None)
    detector.observe(sample, now=0)
    assert detector.observe(sample, now=1000) is False


SILENT = (
    "import json, time\n"
    "print(json.dumps({'output_tokens': 5}), flush=True)\n"
    "time.sleep(60)\n"
)
BUSY = (
    "import json, time\n"
    "for step in range(18):\n"
    "    print(json.dumps({'output_tokens': step + 1}), flush=True)\n"
    "    time.sleep(0.4)\n"
)


def test_runner_raises_harness_stalled_for_a_silent_child(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(settings, "harness_stall_seconds", 2)
    monkeypatch.setattr(settings, "harness_stall_check_seconds", 1.0)
    log = tmp_path / "silent.log"
    with pytest.raises(HarnessStalled) as caught:
        SubprocessRunner(poll_seconds=0.2).run(
            [sys.executable, "-c", SILENT],
            cwd=tmp_path,
            timeout_seconds=60,
            log_path=log,
        )
    assert caught.value.stalled_seconds is not None
    assert "no progress" in str(caught.value)


def test_runner_does_not_kill_a_slow_but_producing_child(tmp_path: Path, monkeypatch):
    """反例：每 0.4 秒仍在产出，远超窗口也不得判 stalled。"""
    monkeypatch.setattr(settings, "harness_stall_seconds", 2)
    monkeypatch.setattr(settings, "harness_stall_check_seconds", 1.0)
    log = tmp_path / "busy.log"
    result = SubprocessRunner(poll_seconds=0.2).run(
        [sys.executable, "-c", BUSY],
        cwd=tmp_path,
        timeout_seconds=60,
        log_path=log,
    )
    assert result.returncode == 0
    assert "output_tokens" in result.stdout
