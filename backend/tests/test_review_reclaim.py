"""A review claim survives its own lease expiring (regression).

``sweep_review_lease`` marks an over-deadline claim ``expired`` so that "the
next reviewer claim starts clean". ``review_claim`` used to INSERT a fresh row
with a hard-coded ``review_revision = 1``, which collides with the unique key
``(task_id, attempt_id, reviewer_harness_id, review_revision)`` -- so once the
sweep had run, the task could never be reviewed again: the claim raised
``sqlite3.IntegrityError`` and the API answered 500 ``internal.error``. Before
the sweep it was just as stuck: the dead row was reused with its stale
``lease_until``, so the submit was refused with ``review.lease_expired``.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.scheduler import sweep_review_lease
from tests.merge_support import REVIEWER, build_chain, status_of


def _backdate_the_claim(chain, *, minutes: float) -> None:
    """Move the claim's deadline into the past so the sweep sees it as over."""
    deadline = (datetime.now(UTC) + timedelta(minutes=minutes)).isoformat()
    with chain.database.transaction() as conn:
        conn.execute(
            "UPDATE reviews SET lease_until = ? WHERE task_id = ?",
            (deadline, chain.task_id),
        )


def _claim(chain, **overrides):
    return chain.service.review_claim(
        task_id=chain.task_id,
        attempt_id=chain.attempt_id,
        reviewer_harness_id=REVIEWER,
        **overrides,
    )


def test_reclaim_after_the_sweep_revives_the_same_review(tmp_path):
    chain = build_chain(tmp_path)
    first = _claim(chain)
    _backdate_the_claim(chain, minutes=-5)
    report = sweep_review_lease(chain.database, datetime.now(UTC))
    assert report.detail["expired"] == [chain.task_id]

    again = _claim(chain)

    assert again["review_id"] == first["review_id"], "the row is taken over, not re-inserted"
    assert again["reused"] is True
    assert datetime.fromisoformat(str(again["lease_until"])) > datetime.now(UTC)

    # The revived claim is submittable -- that is the whole point of the sweep
    # putting the claim "back on the shelf".
    chain.service.submit_review(
        task_id=chain.task_id,
        attempt_id=chain.attempt_id,
        reviewer_harness_id=REVIEWER,
        verdict="approve",
        reviewed_commit=chain.head_commit,
        review_revision=1,
        findings=[],
        review_id=str(again["review_id"]),
    )
    assert status_of(chain) == "approved"


def test_reclaim_before_the_sweep_renews_the_expired_lease(tmp_path):
    """The sweep runs on its own clock; a claim made first must still be usable."""
    chain = build_chain(tmp_path)
    first = _claim(chain)
    _backdate_the_claim(chain, minutes=-5)

    again = _claim(chain)

    assert again["review_id"] == first["review_id"]
    assert datetime.fromisoformat(str(again["lease_until"])) > datetime.now(
        UTC
    ), "a claim past its deadline must hand out a fresh lease, not the dead one"


def test_a_live_lease_is_still_reused_as_is(tmp_path):
    """The retried-claim behaviour the docstring promises is unchanged."""
    chain = build_chain(tmp_path)
    first = _claim(chain)

    again = _claim(chain)

    assert again["review_id"] == first["review_id"]
    assert again["lease_until"] == first["lease_until"]
    assert again["reused"] is True
