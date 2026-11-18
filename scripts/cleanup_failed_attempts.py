"""One-off cleanup: drop the attempt rows that a platform bug produced.

Why: between 2026-11-17 and 2026-11-18 a bug made the harness CLI die in
seconds (the work order was swallowed by a variadic ``--add-dir`` option), so
two tasks were retried hundreds of times.  The office then showed "attempt
360/3" -- a number that is real but says nothing about the work.

What it keeps: the attempt that actually delivered (the one with a
``head_commit`` and a ``verify_passed`` close reason), the task rows, the
event log, and every other task.  What it drops: the failed attempts of
those two tasks, their claim rows, their released leases and their
archived workspaces.

Why this is safe to do directly in the database:

* every lease of these attempts is already **released** (checked first),
* no budget reservation points at them,
* the event log is untouched, and
* this database has **no checkpoint events** (``schema_migration_applied``
  with ``kind=projection.checkpoint.v1``), so the rows are not carried by
  the log -- deleting them does not make them come back on a rebuild.

Run it against a copy first: ``python scripts/cleanup_failed_attempts.py --
dry-run`` prints what it would delete, ``--apply`` does it (after a backup).
"""

from __future__ import annotations

import argparse
import shutil
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

#: The tasks whose attempt history is mostly platform noise.
TARGETS = ("FD-c522372037504337", "FD-fa48d4b2ffe24076")

#: An attempt is worth keeping when it actually produced something: it
#: published a commit and the verification passed.
KEEP_CLOSE_REASON = "verify_passed"


def _connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def plan(conn: sqlite3.Connection) -> dict[str, object]:
    """What would be deleted, and why it is safe to delete it."""
    result: dict[str, object] = {"tasks": {}, "unsafe": []}
    for task_id in TARGETS:
        row = conn.execute(
            "SELECT status, current_attempt_id FROM tasks WHERE task_id = ?", (task_id,)
        ).fetchone()
        if row is None:
            continue
        keep = conn.execute(
            "SELECT attempt_id, attempt_no FROM task_attempts"
            " WHERE task_id = ? AND close_reason = ? AND head_commit IS NOT NULL"
            " ORDER BY attempt_no DESC LIMIT 1",
            (task_id, KEEP_CLOSE_REASON),
        ).fetchone()
        keep_id = str(keep["attempt_id"]) if keep else None
        attempts = conn.execute(
            "SELECT attempt_id FROM task_attempts WHERE task_id = ?", (task_id,)
        ).fetchall()
        drop_ids = [str(a["attempt_id"]) for a in attempts if str(a["attempt_id"]) != keep_id]
        # A lease that is still held would mean the window may still be working
        # in there: that is a reason to stop, not to delete.
        held = conn.execute(
            "SELECT count(*) FROM leases WHERE task_id = ? AND released_at IS NULL", (task_id,)
        ).fetchone()[0]
        if held:
            result["unsafe"].append(f"{task_id}: {held} lease(s) still held")
        result["tasks"][task_id] = {  # type: ignore[index]
            "status": row["status"],
            "keep_attempt": keep_id,
            "drop_attempts": len(drop_ids),
            "held_leases": held,
        }
    return result


#: Which counter each table feeds (the table name is not a safe key:
#: "leases".replace("s", "") is not a word).
_COUNT_KEY = {"task_claims": "claims", "leases": "leases", "workspaces": "workspaces"}

def apply(conn: sqlite3.Connection, task_ids: tuple[str, ...]) -> dict[str, int]:
    """Delete the failed attempts (and what points at them) of these tasks."""
    removed = {
        "attempts": 0,
        "claims": 0,
        "leases": 0,
        "workspaces": 0,
        "artifacts_repointed": 0,
        "artifacts_deleted": 0,
        "attempt_no_reset": 0,
    }
    for task_id in task_ids:
        keep = conn.execute(
            "SELECT attempt_id, attempt_no FROM task_attempts"
            " WHERE task_id = ? AND close_reason = ? AND head_commit IS NOT NULL"
            " ORDER BY attempt_no DESC LIMIT 1",
            (task_id, KEEP_CLOSE_REASON),
        ).fetchone()
        keep_id = str(keep["attempt_id"]) if keep else None
        rows = conn.execute(
            "SELECT attempt_id FROM task_attempts WHERE task_id = ?", (task_id,)
        ).fetchall()
        drop = [str(r["attempt_id"]) for r in rows if str(r["attempt_id"]) != keep_id]
        if not drop:
            continue
        marks = ",".join("?" for _ in drop)
        # The artifacts (git bundle / patch / checkpoint) are **evidence files**
        # of this task; they were written while the failed attempts were
        # archived.  They must not be deleted -- but they may not keep pointing
        # at an attempt that is about to go, or the foreign key breaks.  They
        # move to the attempt that is kept: the task they belong to did not
        # change, only the round that produced them.
        # The artifacts of the dropped rounds (git bundle / patch / checkpoint)
        # point at an attempt that is going away.  They cannot be re-pointed:
        # ``artifacts`` is UNIQUE on (task, attempt, kind, hash) and the failed
        # rounds produced identical bundles, so moving them would collide.
        # They are **rows**, not the evidence itself -- the files stay on disk
        # under ``data/archives/`` and are named after the attempt.
        cur = conn.execute(
            f"DELETE FROM artifacts WHERE attempt_id IN ({marks})", drop  # noqa: S608
        )
        removed["artifacts_deleted"] += cur.rowcount
        for table, key in (
            ("task_claims", "claims"),
            ("leases", "leases"),
            ("workspaces", "workspaces"),
        ):
            cur = conn.execute(
                f"DELETE FROM {table} WHERE attempt_id IN ({marks})", drop  # noqa: S608
            )
            removed[key] += cur.rowcount
        cur = conn.execute(f"DELETE FROM task_attempts WHERE attempt_id IN ({marks})", drop)
        removed["attempts"] += cur.rowcount
        if keep_id:
            # The kept attempt becomes attempt **1** of this task: the rounds
            # that are gone never happened as far as the task is concerned.
            conn.execute(
                "UPDATE task_attempts SET attempt_no = 1 WHERE attempt_id = ?",
                (keep_id,),
            )
        # ``tasks.attempt_no`` is a **stored column** (it goes up by one per
        # claim), so deleting the attempt rows alone would leave the office
        # showing "attempt 360 of 3" -- the number the cleanup was meant to
        # remove.  It now says how many attempts this task really had.
        #
        # 没留下任何 attempt 的任务（取消掉的那种）：计数器归 **0**，不是留在
        # 229 —— 否则一条一条 attempt 都没有的任务，界面上还写着"第 229 次"。
        target_no = 1 if keep_id else 0
        cur = conn.execute(
            "UPDATE tasks SET attempt_no = ? WHERE task_id = ?",
            (target_no, task_id),
        )
        removed["attempt_no_reset"] += cur.rowcount
    return removed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="scripts/cleanup_failed_attempts.py")
    parser.add_argument("--db", default="data/state.db", help="the platform database")
    parser.add_argument("--apply", action="store_true", help="really delete (default: dry run)")
    args = parser.parse_args(argv)

    db_path = Path(args.db)
    if not db_path.exists():
        print(f"database not found: {db_path}", file=sys.stderr)
        return 2
    conn = _connect(db_path)
    try:
        report = plan(conn)
        for task_id, info in report["tasks"].items():  # type: ignore[union-attr]
            print(
                f"{task_id}: status={info['status']} "
                f"keep={info['keep_attempt']} drop={info['drop_attempts']} "
                f"held_leases={info['held_leases']}"
            )
        if report["unsafe"]:
            for line in report["unsafe"]:  # type: ignore[union-attr]
                print(f"UNSAFE: {line}", file=sys.stderr)
            return 1
        if not args.apply:
            print("dry run -- nothing deleted (add --apply to do it)")
            return 0
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        backup = db_path.with_name(db_path.name + f".bak-{stamp}")
        shutil.copy2(db_path, backup)
        print(f"backup: {backup}")
        removed = apply(conn, TARGETS)
        conn.commit()
        print(f"deleted: {removed}")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
