"""黑板与能力报表的只读口径（``SchedulerService.group_stats``）。

三件事被钉在这里：

1. 空库/空群返回零值，不报错；
2. 每个数字都能追到写路径自己留下的记录（提交事件、评审记录点名的轮次、
   放弃事件、领取窗口），不依赖任何页面采样；
3. 端点 ``GET /read/groups/{gid}/stats`` 原样返回同一份聚合。
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from app.db.connection import Database
from app.tasks.service import SchedulerService
from tests.api_support import bootstrap, make_client
from tests.claim_support import make_database, seed_project, seed_task

NOW = datetime(2026, 11, 6, 10, 0, tzinfo=UTC)


def _at(minutes: int) -> str:
    return (NOW + timedelta(minutes=minutes)).isoformat()


def _append(
    database: Database,
    *,
    task_id: str,
    event: str,
    actor: str,
    payload: dict[str, object] | None = None,
    at: str | None = None,
) -> None:
    with database.transaction() as conn:
        conn.execute(
            "INSERT INTO task_events(event_name, aggregate_type, aggregate_id, actor_type,"
            " actor_id, payload_json, payload_hash, created_at)"
            " VALUES (?, 'task', ?, 'harness', ?, ?, 'h', ?)",
            (event, task_id, actor, json.dumps(payload or {}), at or _at(0)),
        )


def _attempt(
    database: Database,
    *,
    task_id: str,
    attempt_id: str,
    attempt_no: int,
    owner: str,
) -> None:
    with database.transaction() as conn:
        conn.execute(
            "INSERT INTO task_attempts(attempt_id, task_id, attempt_no, owner, fencing_epoch,"
            " base_commit, base_locked_seq, head_commit, status, created_at)"
            " VALUES (?, ?, ?, ?, 1, NULL, NULL, NULL, 'in_progress', ?)",
            (attempt_id, task_id, attempt_no, owner, _at(0)),
        )


def _claim(
    database: Database,
    *,
    claim_id: str,
    task_id: str,
    attempt_id: str,
    owner: str,
    start: int,
    end: int,
) -> None:
    with database.transaction() as conn:
        conn.execute(
            "INSERT INTO task_claims(claim_id, task_id, attempt_id, owner, claimed_at,"
            " released_at, release_reason) VALUES (?, ?, ?, ?, ?, ?, 'submitted')",
            (claim_id, task_id, attempt_id, owner, _at(start), _at(end)),
        )


def _rig(tmp_path: Path) -> Database:
    database = make_database(tmp_path)
    seed_project(database)
    return database


def test_an_empty_group_answers_zeros_instead_of_failing(tmp_path):
    database = _rig(tmp_path)

    result = SchedulerService(database).group_stats("G-1")

    assert result == {
        "group_id": "G-1",
        "harnesses": [],
        "totals": {
            "submissions": 0,
            "reworks": 0,
            "abandons": 0,
            "merged": 0,
            "first_pass": 0,
        },
    }


def test_every_number_comes_from_the_write_paths_own_records(tmp_path):
    database = _rig(tmp_path)

    # T-1：第一轮就合入。h1 交了 1 次，做了 120 秒。
    seed_task(database, task_id="T-1", status="merged", attempt_no=1)
    _attempt(database, task_id="T-1", attempt_id="T-1.a1", attempt_no=1, owner="h1")
    _append(database, task_id="T-1", event="submit_result", actor="h1", at=_at(2))
    _claim(
        database,
        claim_id="C-1",
        task_id="T-1",
        attempt_id="T-1.a1",
        owner="h1",
        start=0,
        end=2,
    )
    with database.transaction() as conn:
        conn.execute(
            "UPDATE tasks SET current_attempt_id = 'T-1.a1', owner = 'h1' WHERE task_id = 'T-1'"
        )

    # T-2：第二轮才合入。h1 交两次，被退一次（评审记录点名 a1），做 180 秒。
    seed_task(database, task_id="T-2", status="merged", attempt_no=2)
    _attempt(database, task_id="T-2", attempt_id="T-2.a1", attempt_no=1, owner="h1")
    _attempt(database, task_id="T-2", attempt_id="T-2.a2", attempt_no=2, owner="h1")
    _append(database, task_id="T-2", event="submit_result", actor="h1", at=_at(5))
    _append(
        database,
        task_id="T-2",
        event="request_changes",
        actor="reviewer-1",
        payload={"review_id": "RV-1"},
        at=_at(6),
    )
    _append(database, task_id="T-2", event="submit_result", actor="h1", at=_at(9))
    with database.transaction() as conn:
        conn.execute(
            "INSERT INTO reviews(review_id, task_id, attempt_id, reviewer_harness_id,"
            " reviewer_provider, status, verdict) VALUES ('RV-1', 'T-2', 'T-2.a1',"
            " 'reviewer-1', 'codex', 'completed', 'request_changes')"
        )
        conn.execute(
            "UPDATE tasks SET current_attempt_id = 'T-2.a2', owner = 'h1' WHERE task_id = 'T-2'"
        )
    _claim(
        database,
        claim_id="C-2",
        task_id="T-2",
        attempt_id="T-2.a1",
        owner="h1",
        start=3,
        end=6,
    )

    # T-3：h2 领了 60 秒后放弃。
    seed_task(database, task_id="T-3", status="ready")
    _attempt(database, task_id="T-3", attempt_id="T-3.a1", attempt_no=1, owner="h2")
    _append(database, task_id="T-3", event="abandon", actor="h2", at=_at(21))
    _claim(
        database,
        claim_id="C-3",
        task_id="T-3",
        attempt_id="T-3.a1",
        owner="h2",
        start=20,
        end=21,
    )

    result = SchedulerService(database).group_stats("G-1")
    by_id = {item["harness_id"]: item for item in result["harnesses"]}

    assert by_id["h1"]["submissions"] == 3
    assert by_id["h1"]["reworks"] == 1, "返工记在**被退那轮**的执行者头上，不是评审人"
    assert by_id["h1"]["abandons"] == 0
    assert by_id["h1"]["merged"] == 2
    assert by_id["h1"]["first_pass"] == 1
    assert by_id["h1"]["first_pass_rate"] == 0.5
    assert by_id["h1"]["median_duration_seconds"] == 150.0, "(120 + 180) / 2"

    assert by_id["h2"]["submissions"] == 0
    assert by_id["h2"]["abandons"] == 1
    assert by_id["h2"]["merged"] == 0
    assert by_id["h2"]["first_pass_rate"] is None, "没有合入记录就没有分母"
    assert by_id["h2"]["median_duration_seconds"] == 60.0

    assert result["totals"] == {
        "submissions": 3,
        "reworks": 1,
        "abandons": 1,
        "merged": 2,
        "first_pass": 1,
    }


def test_the_read_endpoint_serves_the_same_numbers(tmp_path):
    client, database = make_client(tmp_path)
    bootstrap(client)

    response = client.get("/api/v1/groups/G-1/stats")

    assert response.status_code == 200, response.text
    assert response.json()["group_id"] == "G-1"
    assert response.json()["harnesses"] == []
    assert response.json()["totals"]["submissions"] == 0


def test_a_task_outside_the_group_never_leaks_into_the_numbers(tmp_path):
    database = _rig(tmp_path)
    with database.transaction() as conn:
        conn.execute(
            "INSERT INTO groups(group_id, project_id, name, created_at)"
            " VALUES ('G-2', 'P-1', 'other', ?)",
            (_at(0),),
        )
    seed_task(database, task_id="T-9", group_id="G-2", status="merged", attempt_no=1)
    _attempt(database, task_id="T-9", attempt_id="T-9.a1", attempt_no=1, owner="h9")
    _append(database, task_id="T-9", event="submit_result", actor="h9")

    result = SchedulerService(database).group_stats("G-1")

    assert result["harnesses"] == []
    assert result["totals"]["submissions"] == 0
