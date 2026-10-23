"""事件载荷不能是整个 transcript。

实测：一次 attempt 的 CLI 输出有 1.3 MB，被整个塞进 ``submit_result`` 事件的
``completion`` 里，于是这条任务的事件流有 2 MB，办公室的读接口直接 502
（网关拒绝过大的上游响应）。

事件日志是**审计轨迹**，不是 transcript：完整的话在
``data/formal-runs/<task>.<attempt>.log`` 与运行证据里，事件只留开头。
"""

from __future__ import annotations

from app.adapter.harnesses.cli import MAX_EVENT_OUTPUT_CHARS, _summarize_output


def test_a_long_string_is_truncated():
    summary = _summarize_output({"final_message": "x" * (MAX_EVENT_OUTPUT_CHARS * 3)})

    assert len(summary["final_message"]) < MAX_EVENT_OUTPUT_CHARS * 2
    assert "truncated" in summary["final_message"]


def test_a_short_output_is_kept_verbatim():
    summary = _summarize_output({"final_message": "done", "session_id": "s-1"})

    assert summary == {"final_message": "done", "session_id": "s-1"}


def test_a_huge_structure_is_replaced_by_its_size():
    summary = _summarize_output({"events": [{"blob": "y" * 1000} for _ in range(50)]})

    assert summary["events"]["truncated"] is True
    assert summary["events"]["bytes"] > MAX_EVENT_OUTPUT_CHARS


def test_numbers_and_booleans_survive():
    summary = _summarize_output({"cost": 0.5, "ok": True, "none": None})

    assert summary == {"cost": 0.5, "ok": True, "none": None}
