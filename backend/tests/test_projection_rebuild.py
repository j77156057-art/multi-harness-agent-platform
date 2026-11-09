"""投影重建：checkpoint + 生命周期事件 → 新库，并与现库逐表 diff。

这两条证明的是 README 的承诺「可从事件重建当前投影」：

* checkpoint 让历史行（没有创建事件、没有 from/to）也能落成完整行；
* checkpoint 之后的真实状态迁移由 reducer 通过 R10 迁移表推进，
  所以不需要每迁一次就再拍一次快照。
"""

from __future__ import annotations

import sqlite3

from app.claim.transitions import apply_transition
from app.events import EventStore
from app.events.projection import GAPS, REBUILDABLE, append_checkpoints, diff, gap_report, rebuild
from app.spec import EventKind, TaskStatus
from tests.claim_support import make_database, seed_project, seed_task


def _seed(tmp_path):
    database = make_database(tmp_path)
    seed_project(database)
    seed_task(database, task_id="T-1")
    return database


def _task_row(path, task_id: str = "T-1") -> dict:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute("SELECT * FROM tasks WHERE task_id = ?", (task_id,)).fetchone()
        return dict(row)
    finally:
        conn.close()


def test_checkpoint_rebuild_is_byte_identical(tmp_path):
    database = _seed(tmp_path)
    written = append_checkpoints(database, actor_id="test")
    assert written >= 3  # project + group + task (+ attempt/lease rows when seeded)

    result = rebuild(database.path, tmp_path / "rebuilt.db")

    assert result.rows["tasks"] == 1
    assert result.checkpoint_events == written
    differences = diff(database.path, result.target)
    failed = {name: item for name, item in differences.items() if not item.ok}
    assert failed == {}, failed


def test_delta_after_checkpoint_advances_status(tmp_path):
    database = _seed(tmp_path)
    append_checkpoints(database, actor_id="test")

    store = EventStore(database)
    with database.transaction() as conn:
        record = store.append(
            event_name=EventKind.CLAIM.value,
            aggregate_type="task",
            aggregate_id="T-1",
            actor_type="harness",
            actor_id="H-1",
            payload={"attempt_id": "T-1.a1", "attempt_no": 1, "fencing_epoch": 1},
            connection=conn,
        )
        apply_transition(
            conn,
            task_id="T-1",
            current=TaskStatus.READY,
            event=EventKind.CLAIM,
            state_seq=record.seq,
        )

    result = rebuild(database.path, tmp_path / "rebuilt.db")
    rebuilt = _task_row(result.target)

    assert rebuilt["status"] == TaskStatus.CLAIMED.value
    assert rebuilt["state_changed_seq"] == record.seq
    # 这条迁移没有改其它列，因此整表应完全一致——不只是状态对。
    differences = diff(database.path, result.target)
    assert all(item.ok for item in differences.values()), {
        name: item.as_dict() for name, item in differences.items() if not item.ok
    }


def test_gap_report_names_the_missing_fact(tmp_path):
    database = _seed(tmp_path)
    report = gap_report(database.path)

    assert set(report) == set(GAPS)
    assert "task_messages" in report
    assert "正文" in report["task_messages"]["gap"]
    assert "auth_tokens" in report
    assert "哈希" in report["auth_tokens"]["gap"]
    assert "tasks" in REBUILDABLE
