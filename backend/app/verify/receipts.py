"""Receipt lookups for the Merge Gate (R10 §16, §21).

R10 §16: "Merge Gate 只认精确匹配待合并 commit 的那一份". A receipt for another
commit, another attempt or another profile is not evidence of anything about the
commit under review, so the lookups here take the commit as a required argument
rather than searching for "the latest receipt".

A receipt exists for failures too -- that is how a task reaches
``changes_requested`` -- so "passing" is ``failure_reason IS NULL``, not "a row
exists".
"""

from __future__ import annotations

import json
import sqlite3

from app.db.connection import Database


def receipt_for(
    database: Database,
    *,
    task_id: str,
    attempt_id: str,
    commit: str,
    verify_profile: str,
) -> dict | None:
    rows = database.read(
        "SELECT * FROM verification_receipts WHERE task_id = ? AND attempt_id = ?"
        ' AND "commit" = ? AND verify_profile = ?',
        (task_id, attempt_id, commit, verify_profile),
    )
    return dict(rows[0]) if rows else None


def passing_receipt_for(database: Database, *, task_id: str, commit: str) -> dict | None:
    """The receipt that would let this exact commit into Review, if any."""
    rows = database.read(
        'SELECT * FROM verification_receipts WHERE task_id = ? AND "commit" = ?'
        # rowid is insertion order; created_seq is NULL until a caller sets it,
        # so ordering by it would be effectively random.
        " AND failure_reason IS NULL ORDER BY rowid",
        (task_id, commit),
    )
    return dict(rows[0]) if rows else None


def merge_is_verifiable(database: Database, *, task_id: str, commit: str) -> tuple[bool, str]:
    """The assertion the Merge Gate is expected to make.

    Returns ``(ok, reason)``. The reason is phrased for an event payload, not for
    a human, so it stays precise about which commit failed to match.
    """
    receipt = passing_receipt_for(database, task_id=task_id, commit=commit)
    if receipt is None:
        return False, f"no passing receipt for commit {commit}"
    if receipt["failure_reason"] is not None:
        return False, f"receipt {receipt['receipt_id']} failed with {receipt['failure_reason']}"
    return True, f"receipt {receipt['receipt_id']} matches {commit}"


def receipt_payload(conn: sqlite3.Connection, receipt_id: str) -> dict:
    """The full §16 receipt as stored, for consumers that need every field."""
    row = conn.execute(
        "SELECT receipt_json FROM verification_receipts WHERE receipt_id = ?", (receipt_id,)
    ).fetchone()
    if row is None:
        raise KeyError(receipt_id)
    return json.loads(str(row["receipt_json"]))
