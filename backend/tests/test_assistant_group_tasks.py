"""OFFICE-UI-35: the assistant's task list comes as a summary, not a cut-off table.

Pinned here:

* the tool hands over **counts, distribution and who holds what** -- and says
  how many rows it left out and how to page through them;
* the model never sees a half table: the observation stays small, and the note
  says the number of rows that are not in this window;
* paging works: ``limit`` / ``offset`` are passed through to the platform and
  the tool says what the window is.
"""

from __future__ import annotations

from typing import Any

from app.assistant.tools import build_registry

GROUP = "g-1"


class Client:
    """A client that serves a task list and records what it was asked for."""

    def __init__(self, tasks: list[dict[str, Any]] | None = None, total: int | None = None):
        self.tasks = tasks if tasks is not None else []
        self.total = len(self.tasks) if total is None else total
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def get(self, path: str, **params: Any) -> dict[str, Any]:
        self.calls.append((path, params))
        if path.endswith("/tasks"):
            # The platform pages: it returns the window, the total, and the
            # counts of the **whole** list.
            offset = int(params.get("offset") or 0)
            limit = params.get("limit")
            window = (
                self.tasks[offset:] if limit is None else self.tasks[offset : offset + int(limit)]
            )
            by_status: dict[str, int] = {}
            unclaimed = 0
            for task in self.tasks:
                state = str(task.get("status") or "unknown")
                by_status[state] = by_status.get(state, 0) + 1
                if not task.get("owner"):
                    unclaimed += 1
            return {
                "tasks": window,
                "total": self.total,
                "by_status": by_status,
                "unclaimed_total": unclaimed,
                "offset": offset,
            }
        return {}

    def post(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        return {}


def _task(index: int, *, owner: str = "", status: str = "ready") -> dict[str, Any]:
    return {
        "task_id": f"FD-{index:016x}",
        "title": f"第 {index} 条活",
        "status": status,
        "owner": owner,
        "assigned_harness_ids": [],
        "exclusive_paths": [],
        "goal": "很长很长的一段目标文字，不该整份进摘要",
    }


def test_the_observation_is_a_summary_not_the_whole_table():
    client = Client([_task(index) for index in range(74)])

    registry = build_registry(client, group_id=GROUP, author_id="office-assistant")
    result = registry["group.tasks"].call({})

    assert result["total"] == 74
    # The distribution is the **whole** list's, not the window's.
    assert result["by_status"] == {"ready": 74}
    # The point of the whole change: the observation is small.
    assert len(repr(result)) < 6000
    # The rows that are not in the window are said out loud.
    assert result["omitted"] == 54
    assert "翻页" in result["note"]


def test_the_summary_says_who_holds_what():
    client = Client([_task(1, owner="opencode", status="in_progress"), _task(2)])

    registry = build_registry(client, group_id=GROUP, author_id="office-assistant")
    result = registry["group.tasks"].call({})

    assert result["claimed"]["count"] == 1
    assert result["claimed"]["items"][0]["owner"] == "opencode"
    assert result["unclaimed"]["count"] == 1
    assert result["unclaimed"]["items"][0]["task_id"] == "FD-0000000000000002"


def test_the_items_carry_only_what_is_needed_to_recognise_a_row():
    client = Client([_task(1)])

    registry = build_registry(client, group_id=GROUP, author_id="office-assistant")
    result = registry["group.tasks"].call({})

    item = result["unclaimed"]["items"][0]
    assert set(item) == {"task_id", "title", "status"}
    assert "goal" not in item
    assert "exclusive_paths" not in item


def test_paging_is_passed_through_to_the_platform():
    client = Client([_task(index) for index in range(74)])

    registry = build_registry(client, group_id=GROUP, author_id="office-assistant")
    result = registry["group.tasks"].call({"limit": 20, "offset": 20})

    path, params = client.calls[-1]
    assert path.endswith("/tasks")
    assert params["limit"] == 20
    assert params["offset"] == 20
    assert result["window"] == {"offset": 20, "limit": 20, "shown": 20}
    assert result["omitted"] == 34
    # The second window carries the next rows -- that is how the model gets all.
    assert result["unclaimed"]["items"][0]["task_id"] == "FD-0000000000000014"


def test_a_nonsense_limit_falls_back_to_the_default_instead_of_failing():
    client = Client([_task(1)])

    registry = build_registry(client, group_id=GROUP, author_id="office-assistant")
    result = registry["group.tasks"].call({"limit": "lots"})

    assert result["window"]["limit"] == 20


def test_a_status_filter_still_works():
    client = Client([_task(1, status="awaiting_review")])

    registry = build_registry(client, group_id=GROUP, author_id="office-assistant")
    result = registry["group.tasks"].call({"status": "awaiting_review"})

    assert client.calls[-1][1]["status"] == "awaiting_review"
    assert result["by_status"] == {"awaiting_review": 1}


def test_a_bad_status_is_refused():
    client = Client([_task(1)])

    registry = build_registry(client, group_id=GROUP, author_id="office-assistant")
    try:
        registry["group.tasks"].call({"status": "Ready; DROP TABLE"})
    except Exception as exc:  # noqa: BLE001
        assert "plain lowercase token" in str(exc)
    else:  # pragma: no cover - the call must not succeed
        raise AssertionError("a bad status must be refused")
