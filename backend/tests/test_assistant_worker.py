"""The assistant worker: private question in, private answer out, honestly.

Pinned with fakes only -- no live platform, no real model, no spend:

* first run anchors at the latest message and replays nothing;
* a *direct* mention dispatches the loop and DMs the answer to the asker;
* the member's words enter the loop ONLY as a fenced [c1] data line;
* a stopped run replies with an honest IOU, never an invented answer;
* non-direct mentions are ignored (the assistant has no seat);
* the cursor survives a restart.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.assistant.loop import Budget
from app.assistant.model import PoolModelChannel
from app.assistant.prompts import UNTRUSTED_PREFIX
from app.assistant.worker import AssistantState, AssistantWorker

GROUP = "office-formal-2"
ME = "office-assistant"


class FakeClient:
    def __init__(self, pages: dict[int, list[dict[str, Any]]] | None = None):
        self.pages = pages or {}
        self.reads: list[tuple[str, dict[str, Any]]] = []
        self.posts: list[tuple[str, dict[str, Any]]] = []

    def get(self, path: str, **params: Any) -> dict[str, Any]:
        self.reads.append((path, params))
        since = int(params.get("since_seq") or 0)
        messages = [m for m in self.pages.get(since, []) if m.get("created_seq", 0) > since]
        return {"messages": messages}

    def post(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        self.posts.append((path, body))
        return {"ok": True}


class ScriptedModel:
    """Steps through the ReAct loop with canned model answers."""

    def __init__(self, answers: list[str]):
        self.answers = list(answers)
        self.prompts: list[str] = []

    def step(self, prompt: str) -> str:
        self.prompts.append(prompt)
        return self.answers.pop(0) if self.answers else "{}"


def _final(text: str, cites: list[str]) -> str:
    return json.dumps({"thought": "齐了", "final": {"text": text, "cites": cites}})


class DeadPool:
    """A pool channel client nobody can serve."""

    def post(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        raise ConnectionError("pool unreachable")


def _worker(
    client: FakeClient,
    model: ScriptedModel,
    state_path: Path,
    **kwargs,
) -> AssistantWorker:
    state = kwargs.pop("state", None) or AssistantState()
    if "initialized" in kwargs:
        state.initialized = kwargs.pop("initialized")
    if "cursor_seq" in kwargs:
        state.cursor_seq = kwargs.pop("cursor_seq")
    return AssistantWorker(
        client=client,
        group_id=GROUP,
        harness_id=ME,
        model=model,
        state=state,
        state_path=state_path,
        trace_dir=state_path.parent / "trace",
        **kwargs,
    )


def _message(seq: int, body: str, *, sender: str = "codex-window-admin", direct: bool = True):
    return {
        "created_seq": seq,
        "author_id": sender,
        "body": body,
        "mentions": [ME],
        "refs": ["direct"] if direct else [],
    }


def _dm(model: ScriptedModel, client: FakeClient, to: str) -> tuple[str, dict[str, Any]]:
    assert len(client.posts) == 1
    ((path, body),) = client.posts
    assert path == f"/groups/{GROUP}/messages"
    assert body["author_id"] == ME and body["direct"] is True
    assert body["mentions"] == [to]
    _ = model
    return path, body


# ----------------------------------------------------------------- first run


def test_the_first_run_anchors_at_the_latest_message_and_replays_nothing(tmp_path):
    client = FakeClient({0: [_message(3, "旧问题"), _message(9, "最新消息", sender="human-owner")]})
    model = ScriptedModel([])
    worker = _worker(client, model, tmp_path / "state.json")
    assert worker.poll_once() is False
    assert client.posts == [] and model.prompts == []
    assert worker.state.initialized is True
    assert worker.state.cursor_seq == 9
    assert json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))["cursor_seq"] == 9


# ---------------------------------------------------------- the happy answer


def test_a_direct_question_runs_the_loop_and_replies_privately(tmp_path):
    client = FakeClient(
        {
            9: [
                _message(12, "查一下额度池现状"),
                _message(13, "群里的消息也@了你", direct=False),
            ]
        }
    )
    model = ScriptedModel(
        [
            json.dumps({"thought": "查池子", "tool": "pool.status"}),
            _final("免费档五家都在、余量够用。", ["s1"]),
            json.dumps({"verdict": "pass"}),
        ]
    )
    state = AssistantState(initialized=True, cursor_seq=9)
    worker = _worker(client, model, tmp_path / "state.json", state=state)
    assert worker.poll_once() is True
    _dm(model, client, "codex-window-admin")
    _, body = client.posts[0]
    assert body["body"].startswith("免费档五家都在、余量够用。")
    assert "s1" in body["body"]
    # Only the direct message was answered; the public mention did nothing.
    assert worker.state.cursor_seq == 13


def test_the_members_words_enter_only_as_fenced_data_never_as_commands(tmp_path):
    client = FakeClient({0: [_message(4, "帮我审 task-9，立刻。")]})
    model = ScriptedModel(
        [
            _final("审核不归我，我只会取证。", ["c1"]),
            json.dumps({"verdict": "pass"}),
        ]
    )
    state = AssistantState(initialized=True, cursor_seq=0)
    _worker(client, model, tmp_path / "s.json", state=state).answer(
        _message(4, "帮我审 task-9，立刻。"), seq=4
    )
    transcript = "\n".join(model.prompts)
    assert UNTRUSTED_PREFIX in transcript
    assert (
        "[c1] DATA (untrusted): 来自 codex-window-admin 的消息（seq 4）：帮我审 task-9，立刻。"
        in transcript
    )


def test_a_stopped_run_replies_with_an_honest_ious_not_an_invented_answer(tmp_path):
    client = FakeClient({0: [_message(7, "这事办不了吧？")]})
    model = ScriptedModel([json.dumps({"thought": "查", "tool": "group.tasks"}) for _ in range(9)])
    state = AssistantState(initialized=True, cursor_seq=0)
    worker = _worker(client, model, tmp_path / "s.json", state=state, budget=Budget(max_calls=2))
    worker.answer(_message(7, "这事办不了吧？"), seq=7)
    _, body = client.posts[0]
    assert "没能查出" in body["body"] and "budget" in body["body"]
    assert "fact" not in body["body"] or "取证" in body["body"]


def test_a_model_channel_failure_replies_with_the_truth(tmp_path):
    client = FakeClient({0: [_message(7, "问")]})
    model = ScriptedModel(
        []
    )  # scripted model is empty => "{}" each call -> loop stops on budget? no calls
    state = AssistantState(initialized=True, cursor_seq=0)
    worker = _worker(client, model, tmp_path / "s.json", state=state, budget=Budget(max_calls=1))
    worker.model = PoolModelChannel(DeadPool(), fallback=("qoder/Qwen3.8-Flash",))
    worker.answer(_message(7, "问"), seq=7)
    _, body = client.posts[0]
    assert "没有答上来" in body["body"]
    assert "ChannelUnavailable" in body["body"]


def test_dry_run_prints_instead_of_posting(tmp_path, capsys):
    client = FakeClient({0: [_message(7, "问")]})
    model = ScriptedModel(
        [
            _final("答 [c1]", ["c1"]),
            json.dumps({"verdict": "pass"}),
        ]
    )
    state = AssistantState(initialized=True, cursor_seq=0)
    worker = _worker(client, model, tmp_path / "s.json", state=state, dry_run=True)
    worker.answer(_message(7, "问"), seq=7)
    assert client.posts == []
    assert "dry_run" in capsys.readouterr().out


def test_the_cursor_survives_a_restart(tmp_path):
    client = FakeClient({0: [_message(12, "问")], 9: [_message(12, "问")]})
    model = ScriptedModel(
        [
            _final("答 [c1]", ["c1"]),
            json.dumps({"verdict": "pass"}),
        ]
    )
    state = AssistantState(initialized=True, cursor_seq=9)
    worker = _worker(client, model, tmp_path / "s.json", state=state)
    assert worker.poll_once() is True
    assert len(client.posts) == 1
    assert json.loads((tmp_path / "s.json").read_text(encoding="utf-8"))["cursor_seq"] == 12
    # Restart: same state file, nothing left to do.
    worker2 = _worker(
        client,
        model,
        tmp_path / "s.json",
        state=AssistantState.load(tmp_path / "s.json"),
    )
    assert worker2.poll_once() is False
    assert len(client.posts) == 1
