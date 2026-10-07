"""MVP-UI-002 (diff-message): the task-thread diff endpoint.

The diff is computed from the canonical object store on demand -- no table,
no event, no invented data. Pinned here:

* stat correctness against a real repository (files, +/- counts, binary);
* the honest unavailable states (nothing published, unknown task/attempt);
* the byte-bounded patch with its explicit ``truncated`` flag;
* the route serving the same reading (and the 404 envelope).
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.api.app import create_app
from app.git.diff import parse_numstat, truncate_patch
from app.git.publish import PublishProxy
from app.git.repo import run_git
from app.tasks.service import NotFound
from tests.git_support import commit_in, make_fixture, provision, seed_and_claim

LOOPBACK = "http://127.0.0.1"


# ------------------------------------------------------------- pure helpers


def test_numstat_marks_binaries_and_totals_the_rest():
    files, insertions, deletions = parse_numstat("3\t1\ta.py\n0\t0\tb.bin\n-\t-\timage.png\n")
    assert [(file.path, file.binary) for file in files] == [
        ("a.py", False),
        ("b.bin", False),
        ("image.png", True),
    ]
    assert files[2].insertions is None and files[2].deletions is None
    assert (insertions, deletions) == (3, 1)

    assert parse_numstat("") == ((), 0, 0)
    assert parse_numstat("garbage line without tabs\n") == ((), 0, 0)


def test_truncate_patch_is_byte_bounded():
    patch = "diff --git a/x b/x\n+" + "字" * 100 + "\n"
    exact, truncated = truncate_patch(patch, len(patch.encode("utf-8")))
    assert exact == patch and truncated is False

    bounded, truncated = truncate_patch(patch, 25)
    assert truncated is True
    assert len(bounded.encode("utf-8")) <= 25, "counting is bytes, not characters"
    assert patch.startswith(bounded)


# ------------------------------------------------------------------- the rig


def _published(tmp_path: Path):
    """claim -> provision -> commit (text + binary) -> publish."""
    fixture = make_fixture(tmp_path)
    # The claim freezes integration_health.integration_commit as base_commit;
    # point it at the canonical repository's real integration head.
    head_ref = fixture.repo.ref_commit(fixture.repo.integration_ref)
    assert head_ref is not None
    with fixture.database.transaction() as conn:
        conn.execute("UPDATE integration_health SET integration_commit = ?", (head_ref,))
    item = seed_and_claim(fixture)
    worktree = provision(fixture, task_id="T-1", attempt_id=item.attempt_id)
    commit_in(worktree, filename="hello.txt", content="hello\nworld\n", message="feat: hello")
    (worktree.path / "blob.bin").write_bytes(b"\x00\x01\x02\xff")
    run_git("add", "blob.bin", cwd=worktree.path)
    run_git("commit", "-m", "feat: binary", cwd=worktree.path)
    head = run_git("rev-parse", "HEAD", cwd=worktree.path).stdout.strip()
    PublishProxy(fixture.database, fixture.repo).publish_task_branch(
        task_id="T-1",
        attempt_id=item.attempt_id,
        branch=worktree.branch,
        commit=head,
        fencing_epoch=item.fencing_epoch,
        idempotency_key="pub-1",
    )
    return fixture, item, worktree, head


# --------------------------------------------------------------- the reading


def test_the_diff_reports_the_published_attempt(tmp_path):
    fixture, item, worktree, head = _published(tmp_path)

    reading = fixture.service.diff_for_task("T-1", repo=fixture.repo)

    assert reading["available"] is True
    assert reading["reason"] is None
    assert reading["attempt_id"] == item.attempt_id
    assert reading["base_commit"] != reading["head_commit"]
    assert reading["head_commit"] == head
    assert reading["branch"] == worktree.branch
    files = {file["path"]: file for file in reading["files"]}
    assert files["hello.txt"]["insertions"] == 2
    assert files["hello.txt"]["deletions"] == 0
    assert files["blob.bin"]["binary"] is True
    assert files["blob.bin"]["insertions"] is None, "binary is unknown, not zero"
    assert reading["files_changed"] == 2
    assert reading["insertions"] == 2
    assert reading["patch"] is None, "the patch rides only on request"
    assert reading["truncated"] is False


def test_the_patch_rides_on_request_with_a_truncation_flag(tmp_path):
    fixture, item, _worktree, _head = _published(tmp_path)

    full = fixture.service.diff_for_task("T-1", include_patch=True, repo=fixture.repo)
    assert full["patch"] is not None and "diff --git" in full["patch"]
    assert full["truncated"] is False

    bounded = fixture.service.diff_for_task(
        "T-1", include_patch=True, repo=fixture.repo, patch_max_bytes=20
    )
    assert bounded["truncated"] is True
    assert len(str(bounded["patch"]).encode("utf-8")) <= 20


def test_an_unpublished_attempt_is_unavailable_not_an_empty_diff(tmp_path):
    fixture = make_fixture(tmp_path)
    item = seed_and_claim(fixture)

    reading = fixture.service.diff_for_task("T-1", repo=fixture.repo)
    assert reading["available"] is False
    assert reading["reason"] == "no attempt has published a commit"
    assert reading["files"] == []

    named = fixture.service.diff_for_task("T-1", attempt_id=item.attempt_id, repo=fixture.repo)
    assert named["available"] is False
    assert named["reason"] == "attempt has not published a commit"


def test_unknown_task_or_attempt_is_not_found(tmp_path):
    fixture = make_fixture(tmp_path)
    seed_and_claim(fixture)

    with pytest.raises(NotFound):
        fixture.service.diff_for_task("NOPE", repo=fixture.repo)
    with pytest.raises(NotFound):
        fixture.service.diff_for_task("T-1", attempt_id="T-1.a99", repo=fixture.repo)


def test_the_route_serves_the_same_reading(tmp_path):
    fixture, item, _worktree, head = _published(tmp_path)
    client = TestClient(create_app(fixture.database, fixture.repo), base_url=LOOPBACK)

    ok = client.get(f"/api/v1/tasks/T-1/diff?include_patch=1&attempt_id={item.attempt_id}")
    assert ok.status_code == 200
    body = ok.json()
    assert body["available"] is True
    assert body["head_commit"] == head
    assert body["patch"].startswith("diff --git")

    missing = client.get("/api/v1/tasks/NOPE/diff")
    assert missing.status_code == 404
    assert missing.json()["error"]["code"]
