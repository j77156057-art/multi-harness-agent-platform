"""MVP-02 storage and event-source tests (DOC-SCHEMA-001/CORE-RECOVER-001)."""

from __future__ import annotations

import sqlite3
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from app.db import Database, MigrationError, apply_migrations
from app.events import EventStore
from app.spec import EventKind, TaskStatus


def test_migrations_are_idempotent_and_enable_wal(tmp_path):
    database = Database(tmp_path / "state.db")
    # 不断言固定数字：新增迁移时本测试不应需要改。首次 initialize 应应用
    # 目录里的**全部**迁移，第二次应为 0（幂等）。
    applied = database.initialize()
    assert applied > 0, "首次 initialize 应至少应用一个迁移"
    assert database.initialize() == 0, "重复 initialize 必须幂等"

    migration_dir = Path(__file__).resolve().parents[1] / "migrations"
    expected = len(list(migration_dir.glob("*.sql")))
    assert applied == expected, f"首次应用了 {applied} 个迁移，但目录里有 {expected} 个 .sql 文件"

    with database.connect() as connection:
        assert connection.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
    assert {"schema_migrations", "tasks", "task_events", "verification_receipts"} <= tables


def test_event_store_is_append_only_and_replayable(tmp_path):
    database = Database(tmp_path / "state.db")
    database.initialize()
    store = EventStore(database)

    first = store.append(
        event_name=EventKind.PLAN_APPROVE,
        aggregate_type="task",
        aggregate_id="T-001",
        actor_type="human",
        actor_id="alice",
        payload={"status": TaskStatus.READY},
    )
    second = store.append(
        event_name=EventKind.CLAIM,
        aggregate_type="task",
        aggregate_id="T-001",
        actor_type="harness",
        actor_id="h-1",
        payload={"attempt_no": 1},
    )

    assert (first.seq, second.seq) == (1, 2)
    assert store.latest_seq() == 2
    assert [event.event_name for event in store.iter_since(0)] == [
        EventKind.PLAN_APPROVE,
        EventKind.CLAIM,
    ]
    replayed = store.replay(lambda state, event: state + [event.event_name], [])
    assert replayed == [EventKind.PLAN_APPROVE, EventKind.CLAIM]

    with database.connect() as connection, pytest.raises(sqlite3.IntegrityError):
        connection.execute("UPDATE task_events SET event_name='tampered' WHERE seq=1")


def test_event_and_domain_write_can_share_one_transaction(tmp_path):
    database = Database(tmp_path / "state.db")
    database.initialize()
    store = EventStore(database)

    with database.transaction() as connection:
        connection.execute(
            "INSERT INTO projects(project_id, name, created_at) VALUES (?, ?, ?)",
            ("p-1", "demo", "2026-01-01T00:00:00+00:00"),
        )
        event = store.append(
            event_name=EventKind.PLAN_APPROVE,
            aggregate_type="project",
            aggregate_id="p-1",
            actor_type="human",
            actor_id="alice",
            connection=connection,
        )
    assert event.seq == 1
    assert database.read("SELECT COUNT(*) AS n FROM projects")[0]["n"] == 1
    assert database.read("SELECT COUNT(*) AS n FROM task_events")[0]["n"] == 1


def test_failed_migration_rolls_back_ddl_and_metadata(tmp_path):
    migrations = tmp_path / "migrations"
    migrations.mkdir()
    script = migrations / "001_broken.sql"
    script.write_text(
        "CREATE TABLE probe (id INTEGER); INSERT INTO missing VALUES (1);", encoding="utf-8"
    )
    database = Database(tmp_path / "state.db")
    with database.connect() as connection, pytest.raises(sqlite3.OperationalError):
        apply_migrations(connection, migrations)
    assert not database.read("SELECT name FROM sqlite_master WHERE name='probe'")
    script.write_text("CREATE TABLE probe (id INTEGER);", encoding="utf-8")
    with database.connect() as connection:
        assert apply_migrations(connection, migrations) == 1
    script.write_text("CREATE TABLE probe (id TEXT);", encoding="utf-8")
    with database.connect() as connection, pytest.raises(MigrationError):
        apply_migrations(connection, migrations)


def test_concurrent_initialization_and_events_have_one_order(tmp_path):
    database = Database(tmp_path / "state.db")
    with ThreadPoolExecutor(max_workers=2) as pool:
        counts = list(pool.map(lambda _: database.initialize(), range(2)))
    # 并发初始化的语义是「恰好一个线程做完全部工作，另一个拿到 0」——
    # 具体数字随迁移数量变化，故只断言相对关系，不写死。
    assert min(counts) == 0, f"应恰好有一个线程无事可做，实际 {counts}"
    assert max(counts) > 0, f"应恰好有一个线程应用了迁移，实际 {counts}"
    store = EventStore(database)

    def append(index):
        return store.append(
            event_name=EventKind.CLAIM,
            aggregate_type="task",
            aggregate_id=f"T-{index}",
            actor_type="harness",
            actor_id="h-1",
            payload={"index": index},
        ).seq

    with ThreadPoolExecutor(max_workers=4) as pool:
        seqs = list(pool.map(append, range(12)))
    assert sorted(seqs) == list(range(1, 13))
    assert [event.seq for event in store.iter_since(6)] == list(range(7, 13))


def test_failed_domain_transaction_has_no_orphan_event(tmp_path):
    database = Database(tmp_path / "state.db")
    database.initialize()
    store = EventStore(database)
    with pytest.raises(RuntimeError), database.transaction() as connection:
        connection.execute(
            "INSERT INTO projects VALUES (?, ?, ?, ?)",
            ("p-1", "demo", "active", "2026-10-05T00:00:00+00:00"),
        )
        store.append(
            event_name=EventKind.PLAN_APPROVE,
            aggregate_type="project",
            aggregate_id="p-1",
            actor_type="human",
            actor_id="alice",
            connection=connection,
        )
        raise RuntimeError("simulated crash before commit")
    assert not database.read("SELECT * FROM projects")
    assert store.latest_seq() == 0


def test_event_delete_unknown_event_and_unscoped_write_are_rejected(tmp_path):
    database = Database(tmp_path / "state.db")
    database.initialize()
    store = EventStore(database)
    arguments = {
        "event_name": EventKind.PLAN_APPROVE,
        "aggregate_type": "task",
        "aggregate_id": "T-1",
        "actor_type": "human",
        "actor_id": "alice",
    }
    with pytest.raises(ValueError):
        store.append(**(arguments | {"event_name": "not-registered"}))
    with database.connect() as connection, pytest.raises(ValueError):
        store.append(**arguments, connection=connection)
    store.append(**arguments)
    with database.transaction() as connection, pytest.raises(sqlite3.IntegrityError):
        connection.execute("DELETE FROM task_events")
    with pytest.raises(sqlite3.OperationalError):
        database.read("DELETE FROM task_events")
    assert store.latest_seq() == 1


def test_discarded_projection_can_be_rebuilt_after_reopening_database(tmp_path):
    path = tmp_path / "state.db"
    database = Database(path)
    database.initialize()
    store = EventStore(database)
    for event, status in [
        (EventKind.PLAN_APPROVE, TaskStatus.READY),
        (EventKind.CLAIM, TaskStatus.CLAIMED),
        (EventKind.START, TaskStatus.IN_PROGRESS),
    ]:
        store.append(
            event_name=event,
            aggregate_type="task",
            aggregate_id="T-1",
            actor_type="scheduler",
            actor_id="local",
            payload={"status": status},
        )

    def reducer(state, event):
        state[event.aggregate_id] = {"status": event.payload["status"], "seq": event.seq}
        return state

    projection = store.replay(reducer, {})
    assert projection == {"T-1": {"status": TaskStatus.IN_PROGRESS, "seq": 3}}
    projection.clear()
    recovered = EventStore(Database(path)).replay(reducer, {})
    assert recovered == {"T-1": {"status": TaskStatus.IN_PROGRESS, "seq": 3}}


def test_global_seq_migration_picks_up_after_legacy_per_group_numbers(tmp_path):
    """003 seeds from the historical max; old per-group message numbers survive."""
    import shutil

    real_dir = Path(__file__).resolve().parents[1] / "migrations"
    legacy_dir = tmp_path / "legacy_migrations"
    legacy_dir.mkdir()
    for name in ("001_initial.sql", "002_task_claims_nullable.sql"):
        shutil.copy(real_dir / name, legacy_dir / name)

    database = Database(tmp_path / "state.db")
    with database.connect() as connection:
        apply_migrations(connection, legacy_dir)

    # Legacy state written by the OLD binary: events 1..5 via AUTOINCREMENT,
    # messages numbered per group -- both groups carry seq 1. Rows are inserted
    # with raw SQL because the current EventStore requires global_seq.
    with database.transaction() as conn:
        for index in range(5):
            conn.execute(
                "INSERT INTO task_events(event_name, aggregate_type, aggregate_id,"
                " actor_type, actor_id, payload_json, payload_hash, schema_version,"
                " created_at) VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?)",
                (
                    EventKind.PLAN_APPROVE.value,
                    "task",
                    f"T-{index}",
                    "human",
                    "alice",
                    "{}",
                    f"hash-{index}",
                    "2026-01-01T00:00:00+00:00",
                ),
            )
        conn.execute(
            "INSERT INTO projects(project_id, name, created_at) VALUES (?, ?, ?)",
            ("p-1", "demo", "2026-01-01T00:00:00+00:00"),
        )
        conn.execute(
            "INSERT INTO groups(group_id, project_id, name, created_at)" " VALUES (?, ?, ?, ?)",
            ("g-a", "p-1", "a", "2026-01-01T00:00:00+00:00"),
        )
        conn.execute(
            "INSERT INTO groups(group_id, project_id, name, created_at)" " VALUES (?, ?, ?, ?)",
            ("g-b", "p-1", "b", "2026-01-01T00:00:00+00:00"),
        )
        for group in ("g-a", "g-b"):
            conn.execute(
                "INSERT INTO task_messages(message_id, group_id, sender_type,"
                " sender_id, body, created_seq, created_at)"
                " VALUES (?, ?, 'human', 'h', 'x', 1, '2026-01-01T00:00:00+00:00')",
                (f"msg-{group}", group),
            )

    with database.connect() as connection:
        applied = apply_migrations(connection, real_dir)
    assert applied == 1

    with database.connect() as conn:
        assert int(conn.execute("SELECT seq FROM global_seq WHERE id=1").fetchone()[0]) == 5
        assert conn.execute("SELECT COUNT(*) AS n FROM task_events").fetchone()[0] == 5
        assert (
            "AUTOINCREMENT"
            not in conn.execute(
                "SELECT sql FROM sqlite_master WHERE name='task_events'"
            ).fetchone()[0]
        )

    store = EventStore(database)
    next_event = store.append(
        event_name=EventKind.CLAIM,
        aggregate_type="task",
        aggregate_id="T-new",
        actor_type="harness",
        actor_id="h-1",
        payload={},
    )
    assert next_event.seq == 6

    from app.tasks.service import SchedulerService

    message_seq = SchedulerService(database).post_message(
        group_id="g-a",
        author_kind="human",
        author_id="h",
        body="after upgrade",
        mentions=[],
    )["seq"]
    assert message_seq == 7


def test_foreign_keys_and_sql_keyword_columns_are_usable(tmp_path):
    database = Database(tmp_path / "state.db")
    database.initialize()
    with database.transaction() as connection, pytest.raises(sqlite3.IntegrityError):
        connection.execute(
            "INSERT INTO groups VALUES (?, ?, ?, ?, ?)",
            ("g-1", "missing", "group", "active", "now"),
        )
    assert database.read('SELECT "commit" FROM verification_receipts') == []
    assert database.read("PRAGMA foreign_key_check") == []
