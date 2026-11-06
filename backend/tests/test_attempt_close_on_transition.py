"""任务进入终态（或离开 verifying）时，attempt 必须由状态机收口。

2026-11-05 的现场有两类漏账：

* 8 条 attempt 停在 ``verifying``、``closed_at`` 为空，任务却已经
  ``awaiting_review`` / ``approved`` —— verify 已经给出结论，但没有任何人
  关 attempt；
* 67 行「任务已终态 + attempt 仍开着」的历史违反（本轮只读，不回填）。

这里守的是「以后不再产生」：收口点只有 ``apply_transition`` 一处，凡是任务
落到 merged / cancelled / abandoned / escalated / changes_requested / stale /
reclaiming / ready，或者 verify_passed 把任务带离 verifying，都要落
``closed_at`` 与可区分路径的 ``close_reason``。
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.claim.transitions import apply_transition
from app.scheduler.sweeps import sweep_attempt_reconcile
from app.spec import EventKind, TaskStatus
from app.tasks.service import SchedulerService
from tests.claim_support import make_database, seed_project, seed_task, task_row

ATTEMPT_ID = "T-1.a1"


@pytest.fixture()
def open_attempt(tmp_path):
    """A database plus a seeder for task T-1 with one open attempt."""
    database = make_database(tmp_path)

    def seed(*, task_status: str, attempt_status: str) -> None:
        seed_project(database)
        seed_task(database, status=task_status)
        with database.transaction() as conn:
            conn.execute(
                "INSERT INTO task_attempts (attempt_id, task_id, attempt_no, owner,"
                " fencing_epoch, base_commit, head_commit, status, created_at)"
                " VALUES (?, 'T-1', 1, 'harness-1', 0, 'base-commit-0001',"
                " 'head-commit-0001', ?, datetime('now'))",
                (ATTEMPT_ID, attempt_status),
            )
            conn.execute(
                "UPDATE tasks SET current_attempt_id = ?, current_epoch = 0,"
                " owner = 'harness-1' WHERE task_id = 'T-1'",
                (ATTEMPT_ID,),
            )

    yield database, seed


def _attempt(database) -> dict[str, object]:
    rows = database.read("SELECT * FROM task_attempts WHERE attempt_id = ?", (ATTEMPT_ID,))
    assert rows, "attempt row disappeared"
    return dict(rows[0])


def _transition(database, *, current: TaskStatus, event: EventKind, **kwargs) -> None:
    with database.transaction() as conn:
        apply_transition(
            conn,
            task_id="T-1",
            current=current,
            event=event,
            **kwargs,
        )


def test_verify_passed_closes_the_attempt_that_was_sitting_in_verifying(open_attempt):
    """8 条卡 verifying 的根因：任务已到 awaiting_review，attempt 还开着。"""
    database, seed = open_attempt
    seed(task_status="verifying", attempt_status="verifying")

    _transition(database, current=TaskStatus.VERIFYING, event=EventKind.VERIFY_PASSED)

    assert task_row(database, "T-1")["status"] == "awaiting_review"
    row = _attempt(database)
    assert row["closed_at"] is not None
    assert row["status"] == "released"
    assert row["close_reason"] == "verify_passed"


@pytest.mark.parametrize(
    ("current", "event", "attempt_status"),
    [
        (TaskStatus.VERIFYING, EventKind.VERIFY_EXECUTION_TIMEOUT, "verifying"),
        (TaskStatus.VERIFYING, EventKind.VERIFY_QUEUE_TIMEOUT, "verifying"),
        (TaskStatus.AWAITING_REVIEW, EventKind.REVIEW_WAIT_TIMEOUT, "verifying"),
        (TaskStatus.AWAITING_REVIEW, EventKind.REVIEW_ROUND_EXHAUSTED, "verifying"),
        (TaskStatus.MERGING, EventKind.MERGE_TIMEOUT, "verifying"),
        (TaskStatus.IN_PROGRESS, EventKind.STUCK_THRESHOLD_REACHED, "in_progress"),
    ],
)
def test_every_escalation_edge_closes_the_attempt(
    open_attempt, current, event, attempt_status
):
    """升级不是「先挂着」：任务交给人类时，attempt 必须已经关掉。"""
    database, seed = open_attempt
    seed(task_status=current.value, attempt_status=attempt_status)

    _transition(database, current=current, event=event)

    assert task_row(database, "T-1")["status"] == "escalated"
    row = _attempt(database)
    assert row["closed_at"] is not None
    assert row["status"] == "escalated"
    assert row["close_reason"] == event.value


@pytest.mark.parametrize(
    ("current", "event", "attempt_status", "closed_as"),
    [
        (TaskStatus.MERGING, EventKind.MERGE_SUCCESS, "verifying", "merged"),
        (TaskStatus.IN_PROGRESS, EventKind.CANCEL, "in_progress", "cancelled"),
        (TaskStatus.IN_PROGRESS, EventKind.ABANDON, "in_progress", "abandoned"),
        (TaskStatus.VERIFYING, EventKind.VERIFY_CODE_FAILED, "verifying", "failed"),
        (TaskStatus.AWAITING_HUMAN, EventKind.LEASE_EXPIRE, "in_progress", "failed"),
        (TaskStatus.STALE, EventKind.RECLAIM, "claimed", "failed"),
        (TaskStatus.ESCALATED, EventKind.HUMAN_RELEASE, "verifying", "released"),
        (TaskStatus.BLOCKED, EventKind.ALL_CHILDREN_MERGED, "in_progress", "released"),
    ],
)
def test_every_terminal_edge_closes_the_attempt(
    open_attempt, current, event, attempt_status, closed_as
):
    """其余终态路径：交付、取消、放弃、退回、失联、回收、放行。"""
    database, seed = open_attempt
    seed(task_status=current.value, attempt_status=attempt_status)

    _transition(database, current=current, event=event)

    row = _attempt(database)
    assert row["closed_at"] is not None
    assert row["status"] == closed_as
    assert row["close_reason"] == event.value


def test_a_self_loop_leaves_a_legacy_open_attempt_alone(open_attempt):
    """提醒类自环不是「进入终态」：历史数据保持只读，不借 sweep 回填。"""
    database, seed = open_attempt
    seed(task_status="escalated", attempt_status="in_progress")

    _transition(
        database,
        current=TaskStatus.ESCALATED,
        event=EventKind.ESCALATION_REMINDER,
    )

    row = _attempt(database)
    assert row["closed_at"] is None
    assert row["status"] == "in_progress"


def test_a_paused_verifying_attempt_is_not_closed(open_attempt):
    """differential_not_applicable 只是暂停，人类 resume 后还用同一个 attempt。"""
    database, seed = open_attempt
    seed(task_status="verifying", attempt_status="verifying")

    _transition(
        database,
        current=TaskStatus.VERIFYING,
        event=EventKind.DIFFERENTIAL_NOT_APPLICABLE,
    )

    assert task_row(database, "T-1")["status"] == "awaiting_human"
    assert _attempt(database)["closed_at"] is None


def test_release_keeps_the_humans_own_words(open_attempt):
    """释放路径：close_reason 写人给的那句话；current_attempt_id 同句清空也能读到。"""
    database, seed = open_attempt
    seed(task_status="in_progress", attempt_status="in_progress")
    stated = "GitError: git checkout -b fd-283b9880abb0ab34-a1 FETCH_HEAD failed"

    _transition(
        database,
        current=TaskStatus.IN_PROGRESS,
        event=EventKind.RELEASE,
        expected_epoch=0,
        extra_sets={"owner": None, "current_attempt_id": None},
        attempt_close_reason=stated,
    )

    assert task_row(database, "T-1")["status"] == "ready"
    row = _attempt(database)
    assert row["closed_at"] is not None
    assert row["status"] == "released"
    assert row["close_reason"] == stated


def test_the_service_release_path_closes_an_in_progress_attempt(open_attempt):
    """端到端：SchedulerService.release 不再自己关 attempt，收口在状态机里。"""
    database, seed = open_attempt
    seed(task_status="in_progress", attempt_status="in_progress")

    SchedulerService(database).release(
        task_id="T-1",
        attempt_id=ATTEMPT_ID,
        fencing_epoch=0,
        reason="relay_cli_cannot_use_tools",
    )

    assert task_row(database, "T-1")["status"] == "ready"
    row = _attempt(database)
    assert row["closed_at"] is not None
    assert row["status"] == "released"
    assert row["close_reason"] == "relay_cli_cannot_use_tools"


def test_the_reconcile_sweep_makes_a_bypassed_close_visible(tmp_path):
    """只读 sweep：awaiting_review 还开着 attempt 会被点名，正常在途的不算。"""
    database = make_database(tmp_path)
    seed_project(database)
    seed_task(database, task_id="T-1", status="awaiting_review")
    seed_task(database, task_id="T-2", status="in_progress")
    with database.transaction() as conn:
        for attempt_id, task_id, status in (
            ("T-1.a1", "T-1", "verifying"),
            ("T-2.a1", "T-2", "in_progress"),
        ):
            conn.execute(
                "INSERT INTO task_attempts (attempt_id, task_id, attempt_no, owner,"
                " fencing_epoch, base_commit, head_commit, status, created_at)"
                " VALUES (?, ?, 1, 'harness-1', 0, 'base-commit-0001',"
                " 'head-commit-0001', ?, datetime('now'))",
                (attempt_id, task_id, status),
            )

    report = sweep_attempt_reconcile(database, now=datetime.now(UTC))

    assert report.sweep == "attempt_reconcile"
    assert report.actions == 0, "只读观测，不写库"
    assert report.detail == {
        "open_attempts": 1,
        "by_task_status": {"awaiting_review": 1},
    }
    rows = database.read(
        "SELECT closed_at FROM task_attempts WHERE attempt_id = 'T-1.a1'"
    )
    assert rows[0]["closed_at"] is None, "sweep 不得替漏账补写"
