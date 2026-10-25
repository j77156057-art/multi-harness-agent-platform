"""OFFICE-UI-34: the assistant's memory and the trace summary the office shows.

Two things are pinned here:

* **memory** -- the turns of the same private thread, in order, without the
  current question, formatted as the engine's citation lines (so the gate that
  checks every citation still resolves);
* **the trace summary** -- what the assistant did in a turn, with the raw
  observation cut down to a size note and a head, not the whole 256KB dump.
"""

from __future__ import annotations

import json

from app.assistant.memory import memory_context, thread_turns
from app.assistant.runs import read_runs

ASSISTANT = "office-assistant"


def _message(seq, sender, body, *, direct=True):
    return {
        "created_seq": seq,
        "sender_id": sender,
        "body": body,
        "refs": ["direct"] if direct else [],
    }


def test_the_memory_is_the_same_private_thread_in_order():
    messages = [
        _message(10, "office-operator", "第一条"),
        _message(11, ASSISTANT, "第一条的回答"),
        _message(12, "office-operator", "群里的公开消息", direct=False),
        _message(13, "office-operator", "第二条"),
    ]

    turns = thread_turns(messages, before_seq=99)

    assert [turn["created_seq"] for turn in turns] == [10, 11, 13]
    assert "群里的公开消息" not in " ".join(turn["body"] for turn in turns)


def test_the_memory_leaves_the_current_question_out():
    messages = [
        _message(10, "office-operator", "第一问"),
        _message(11, ASSISTANT, "第一答"),
        _message(12, "office-operator", "这是本轮"),
    ]

    turns = thread_turns(messages, before_seq=12)

    assert [turn["created_seq"] for turn in turns] == [10, 11]
    assert all(turn["created_seq"] < 12 for turn in turns)


def test_the_memory_lines_say_who_said_what():
    messages = [
        _message(10, "office-operator", "第一问"),
        _message(11, ASSISTANT, "第一答"),
    ]

    lines = memory_context(thread_turns(messages), assistant_id=ASSISTANT)

    assert len(lines) == 2
    assert "对方" in lines[0] and "第一问" in lines[0]
    assert "助手" in lines[1] and "第一答" in lines[1]


def test_the_memory_is_bounded():
    messages = [_message(index, "office-operator", f"第 {index} 轮") for index in range(50)]

    turns = thread_turns(messages, limit=3)
    lines = memory_context(turns, assistant_id=ASSISTANT, limit=3)

    assert len(turns) == 3
    assert len(lines) == 3


def test_a_long_turn_is_clipped():
    messages = [_message(10, "office-operator", "长" * 2000)]

    lines = memory_context(thread_turns(messages), assistant_id=ASSISTANT)

    assert len(lines[0]) < 1000
    assert lines[0].endswith("…")


def _write_trace(directory, name, rows):
    path = directory / f"{name}.jsonl"
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    return path


def test_the_trace_summary_says_what_it_did(tmp_path):
    trace = tmp_path / "assistant" / "trace"
    trace.mkdir(parents=True)
    _write_trace(
        trace,
        "run-1",
        [
            {
                "no": 1,
                "kind": "act",
                "thought": "缺任务表",
                "tool": "group.tasks",
                "observation": {"tasks": [1, 2, 3]},
            },
            {
                "run_id": "run-1",
                "finished_at": 1.0,
                "status": "done",
                "model": "some/model",
                "calls_used": 1,
            },
        ],
    )

    runs = read_runs(tmp_path)

    assert len(runs) == 1
    assert runs[0]["run_id"] == "run-1"
    assert runs[0]["status"] == "done"
    assert runs[0]["steps"][0]["tool"] == "group.tasks"
    assert "缺任务表" in runs[0]["steps"][0]["thought"]
    assert "字符" in runs[0]["steps"][0]["observation"]


def test_the_trace_summary_cuts_a_huge_observation_down(tmp_path):
    trace = tmp_path / "assistant" / "trace"
    trace.mkdir(parents=True)
    _write_trace(
        trace,
        "run-2",
        [
            {
                "no": 1,
                "kind": "act",
                "tool": "task.forensics",
                "observation": {"text": "x" * 200000},
            },
            {"run_id": "run-2", "status": "done"},
        ],
    )

    runs = read_runs(tmp_path)

    observation = runs[0]["steps"][0]["observation"]
    assert len(observation) < 1000
    # The size note is there, so the reader knows the observation was cut.
    assert "2000" in observation


def test_no_trace_directory_is_not_an_error(tmp_path):
    assert read_runs(tmp_path) == []
