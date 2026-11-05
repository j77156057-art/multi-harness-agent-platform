"""2026-11-05 drill: worktree recycling must preserve work, and capacity must
follow *live leases* instead of leftover directories.

The E2E incident: 8 historical workspaces (all with released, long-expired
leases) filled the default cap of 8, so every new task died with
``WorktreeCapacityExceeded``.  A CodeBuddy run had also lost 30 files because
the archive only packed committed branches.
"""

from __future__ import annotations

import threading
from datetime import UTC, datetime, timedelta


from app.adapter.dispatch import eligible_tasks
from app.claim.service import (
    OUTCOME_QUEUED,
    ClaimRequest,
    ClaimService,
)
from app.git.repo import run_git
from app.git.worktree import (
    WorktreeManager,
    worktree_capacity,
)
from app.scheduler import sweep_resources, sweep_worktree_recycle
from tests.claim_support import seed_task
from tests.git_support import base_commit, commit_in, make_fixture, provision, seed_and_claim


def _archive_root(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("app.config.settings.data_dir", tmp_path / "data")


def _live_lease(database, attempt_id: str) -> dict[str, object]:
    return database.read(
        "SELECT lease_until, owner, released_at FROM leases"
        " WHERE attempt_id = ? AND kind = 'development'"
        " ORDER BY rowid DESC LIMIT 1",
        (attempt_id,),
    )[0]


def _expire_lease(database, attempt_id: str) -> str:
    past = (datetime.now(UTC) - timedelta(hours=2)).isoformat()
    with database.transaction() as conn:
        conn.execute(
            "UPDATE leases SET released_at = NULL, lease_until = ?"
            " WHERE attempt_id = ? AND kind = 'development'",
            (past, attempt_id),
        )
    return past


def test_expired_lease_slot_is_recycled_and_capacity_returns(tmp_path, monkeypatch):
    """租约过期 + 归档完成 → 工位回收，容量回到可派新任务。"""
    _archive_root(tmp_path, monkeypatch)
    monkeypatch.setattr("app.config.settings.concurrent_worktree", 1)
    fixture = make_fixture(tmp_path)
    first = seed_and_claim(fixture, task_id="T-1")
    worktree = provision(fixture, task_id="T-1", attempt_id=first.attempt_id)
    with fixture.database.transaction() as conn:
        conn.execute("UPDATE tasks SET status = 'in_progress' WHERE task_id = 'T-1'")
    _expire_lease(fixture.database, first.attempt_id)

    # B: an expired lease no longer holds the only slot even though the old
    # directory is still on disk.  Prove it *before* the sweep archives it.
    with fixture.database.connect() as conn:
        capacity = worktree_capacity(conn, limit=1)
    assert capacity["active"] == 0
    assert capacity["at_capacity"] is False
    seed_task(fixture.database, task_id="T-2")
    second = ClaimService(fixture.database).claim(
        ClaimRequest(claim_request_id="cr-T-2", harness_id="harness-1", task_ids=("T-2",))
    ).items[0]
    assert second.attempt_id is not None, "the expired slot no longer occupies the cap"
    new_tree = provision(fixture, task_id="T-2", attempt_id=second.attempt_id)
    assert new_tree.path.exists()

    # C: the sweeper then archives the dead tree and frees its directory.
    report = sweep_worktree_recycle(
        fixture.database, datetime.now(UTC), repo=fixture.repo, root=fixture.manager.root
    )
    assert report.detail["recycled"] == [first.attempt_id]
    assert not worktree.path.exists()
    assert not worktree.gitdir.exists()
    rows = fixture.database.read(
        "SELECT attempt_id, status FROM workspaces ORDER BY rowid"
    )
    assert [(row["attempt_id"], row["status"]) for row in rows] == [
        (first.attempt_id, "archived"),
        (second.attempt_id, "active"),
    ]


def test_capacity_full_queues_claim_with_reason_and_earliest(tmp_path, monkeypatch):
    """容量满时不建 attempt/lease/workspace，回队列并给原因与最早可用时间。"""
    _archive_root(tmp_path, monkeypatch)
    monkeypatch.setattr("app.config.settings.concurrent_worktree", 1)
    fixture = make_fixture(tmp_path)
    held = seed_and_claim(fixture, task_id="T-1")
    provision(fixture, task_id="T-1", attempt_id=held.attempt_id)
    lease = _live_lease(fixture.database, held.attempt_id)
    assert lease["released_at"] is None

    seed_task(fixture.database, task_id="T-2")
    result = ClaimService(fixture.database).claim(
        ClaimRequest(claim_request_id="cr-T-2", harness_id="harness-1", task_ids=("T-2",))
    )

    assert result.items[0].outcome == OUTCOME_QUEUED
    item = result.items[0]
    assert item.queue_reason == "worktree_capacity"
    assert item.earliest_available_at == lease["lease_until"]
    assert item.unavailability_reasons
    assert held.attempt_id in item.unavailability_reasons[0]
    assert item.attempt_id is None
    assert item.lease_token is None

    task = fixture.database.read(
        "SELECT status, queue_reason, owner, current_attempt_id FROM tasks WHERE task_id = 'T-2'"
    )[0]
    assert task["status"] == "queued"
    assert task["queue_reason"] == "worktree_capacity"
    assert task["owner"] is None
    assert task["current_attempt_id"] is None
    assert fixture.database.read(
        "SELECT COUNT(*) AS n FROM task_attempts WHERE task_id = 'T-2'"
    )[0]["n"] == 0
    assert fixture.database.read(
        "SELECT COUNT(*) AS n FROM leases WHERE task_id = 'T-2'"
    )[0]["n"] == 0
    assert fixture.database.read(
        "SELECT COUNT(*) AS n FROM workspaces WHERE task_id = 'T-2'"
    )[0]["n"] == 0

    deferred = fixture.database.read(
        "SELECT release_reason FROM task_claims WHERE task_id = 'T-2'"
    )[0]
    assert deferred["release_reason"] == "queued:worktree_capacity"


def test_queued_worktree_claim_is_completed_by_its_worker_not_opened_by_the_sweep(
    tmp_path, monkeypatch
):
    """容量满排队 → 容量释放 → 排队方自己完成领取并开工，不停在 claimed。

    2026-11-06 现场：``sweep_resources`` 替排队的 claim 开了 attempt，却没有
    worker 去跑它 —— 任务停在 claimed，工位没建、harness 没跑。修复后：
    sweep 不再碰 worktree 队列；排队方在 ``list_ready_tasks`` 里重新看到自己
    的任务，用原 claim_request_id 重试，领取在同一个事务里完成，随后
    ``start_task`` 建工位并把任务推进到 in_progress。
    """
    _archive_root(tmp_path, monkeypatch)
    monkeypatch.setattr("app.config.settings.concurrent_worktree", 1)
    fixture = make_fixture(tmp_path)
    held = seed_and_claim(fixture, task_id="T-1")
    provision(fixture, task_id="T-1", attempt_id=held.attempt_id)

    seed_task(fixture.database, task_id="T-2")
    service = ClaimService(fixture.database)
    request_id = "formal-T-2-a1"  # formal_worker 的确定性 request id
    queued = service.claim(
        ClaimRequest(
            claim_request_id=request_id, harness_id="harness-1", task_ids=("T-2",)
        )
    )
    assert queued.items[0].outcome == OUTCOME_QUEUED

    # 容量释放：占位者的开发租约被释放（工位目录暂未回收）。
    with fixture.database.transaction() as conn:
        conn.execute(
            "UPDATE leases SET released_at = ?"
            " WHERE attempt_id = ? AND kind = 'development'",
            (datetime.now(UTC).isoformat(), held.attempt_id),
        )

    # 1) sweeper 不得替它开 attempt —— 开了就没人跑。
    report = sweep_resources(fixture.database, datetime.now(UTC))
    assert report.actions == 0
    assert (
        fixture.database.read(
            "SELECT COUNT(*) AS n FROM task_attempts WHERE task_id = 'T-2'"
        )[0]["n"]
        == 0
    ), "the sweep must not open an attempt nobody will provision"
    still = fixture.database.read(
        "SELECT status, owner, current_attempt_id FROM tasks WHERE task_id = 'T-2'"
    )[0]
    assert still["status"] == "queued"
    assert still["owner"] is None and still["current_attempt_id"] is None

    # 2) 排队方在自己的待办里重新看到它（别的窗口看不到）。
    mine = eligible_tasks(fixture.database, harness_id="harness-1", group_id=None, limit=10)
    assert [row["task_id"] for row in mine] == ["T-2"]
    theirs = eligible_tasks(fixture.database, harness_id="harness-2", group_id=None, limit=10)
    assert [row["task_id"] for row in theirs] == []

    # 3) 原 id 重试 → 原地完成领取（不是 replay 出 queued），worker 立刻开工。
    retried = service.claim(
        ClaimRequest(
            claim_request_id=request_id, harness_id="harness-1", task_ids=("T-2",)
        )
    ).items[0]
    assert retried.outcome == "claimed"
    assert retried.attempt_id == "T-2.a1"
    assert retried.lease_token, "the retried claim mints a usable lease"

    fixture.service.start_task(
        task_id="T-2",
        attempt_id=retried.attempt_id,
        fencing_epoch=retried.fencing_epoch,
        group_short="g1",
        worktrees=fixture.manager,
    )
    assert [
        row["status"]
        for row in fixture.database.read("SELECT status FROM tasks WHERE task_id = 'T-2'")
    ] == ["in_progress"], "the task left `claimed`: the worker actually started"
    workspace = fixture.manager.workspace_for_attempt("T-2.a1")
    assert workspace is not None and workspace.path.exists(), "the worktree was built"

    # 4) 再扫也不会重复开 attempt。
    assert sweep_resources(fixture.database, datetime.now(UTC)).actions == 0
    assert (
        fixture.database.read(
            "SELECT COUNT(*) AS n FROM task_attempts WHERE task_id = 'T-2'"
        )[0]["n"]
        == 1
    )


def test_two_concurrent_claims_cannot_share_one_unprovisioned_slot(tmp_path, monkeypatch):
    """cap=1、claim 与 provision 之间：租约本身就是占位，后到者排队而不是开 attempt。

    2026-11-06 现场：容量只数 workspaces 行，两个 claim 在 17:23:00.443/.588
    都通过；后到者到 provision 才撞 ``WorktreeCapacityExceeded``，留下
    ``close_reason NULL`` 的孤儿 attempt。修复后领取时的容量判定把「已 claim
    未 provision」的活租约算进来，恰好一个成功、另一个进队列，0 attempt。
    """
    _archive_root(tmp_path, monkeypatch)
    monkeypatch.setattr("app.config.settings.concurrent_worktree", 1)
    fixture = make_fixture(tmp_path)
    seed_task(fixture.database, task_id="T-1")
    seed_task(fixture.database, task_id="T-2")

    results: dict[str, object] = {}

    def claim(task_id: str) -> None:
        try:
            results[task_id] = ClaimService(fixture.database).claim(
                ClaimRequest(
                    claim_request_id=f"formal-{task_id}-a1",
                    harness_id="harness-1",
                    task_ids=(task_id,),
                )
            ).items[0]
        except Exception as exc:  # pragma: no cover - a raised claim is a failure
            results[task_id] = exc

    threads = [
        threading.Thread(target=claim, args=(task_id,)) for task_id in ("T-1", "T-2")
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    items = [results[task_id] for task_id in ("T-1", "T-2")]
    assert [type(item).__name__ for item in items] == ["ClaimItem", "ClaimItem"]
    assert sorted(item.outcome for item in items) == ["claimed", "queued"]
    winner = next(item for item in items if item.outcome == "claimed")
    loser = next(item for item in items if item.outcome == "queued")
    assert loser.queue_reason == "worktree_capacity"

    # 后到者干净失败：没有 attempt，也没有悬空的 close_reason。
    assert (
        fixture.database.read(
            "SELECT COUNT(*) AS n FROM task_attempts WHERE task_id = ?",
            (loser.task_id,),
        )[0]["n"]
        == 0
    )
    open_attempts = fixture.database.read(
        "SELECT attempt_id FROM task_attempts WHERE status = 'claimed'"
    )
    assert [row["attempt_id"] for row in open_attempts] == [winner.attempt_id]
    assert loser.attempt_id is None and loser.lease_token is None

    # 先到者仍然能拿到自己占的那个位子：provision 必须排除它自己的租约。
    tree = provision(fixture, task_id=winner.task_id, attempt_id=winner.attempt_id)
    assert tree.path.exists()

    # 位子现在真的被占住了：再来一条只能排队。
    seed_task(fixture.database, task_id="T-3")
    third = ClaimService(fixture.database).claim(
        ClaimRequest(claim_request_id="cr-T-3", harness_id="harness-1", task_ids=("T-3",))
    ).items[0]
    assert third.outcome == "queued"
    assert third.queue_reason == "worktree_capacity"


def test_orphan_directory_with_uncommitted_work_is_kept_then_clean_one_is_archived(
    tmp_path, monkeypatch
):
    """目录残留但无租约：脏的绝不动，干净的先 bundle 再清理。"""
    _archive_root(tmp_path, monkeypatch)
    fixture = make_fixture(tmp_path)
    item = seed_and_claim(fixture)
    worktree = provision(fixture, task_id="T-1", attempt_id=item.attempt_id)
    with fixture.database.transaction() as conn:
        conn.execute("DELETE FROM workspaces WHERE attempt_id = ?", (item.attempt_id,))

    (worktree.path / "dirty.txt").write_text("unarchived work\n", encoding="utf-8")
    report = sweep_worktree_recycle(
        fixture.database, datetime.now(UTC), repo=fixture.repo, root=fixture.manager.root
    )

    assert report.detail["orphans_removed"] == []
    kept = report.detail["orphans_kept"]
    assert kept == [{"path": str(worktree.path), "reason": "uncommitted_changes"}]
    assert worktree.path.exists(), "dirty orphan must never be deleted"

    (worktree.path / "dirty.txt").unlink()
    report = sweep_worktree_recycle(
        fixture.database, datetime.now(UTC), repo=fixture.repo, root=fixture.manager.root
    )

    assert report.detail["orphans_removed"] == [str(worktree.path)]
    assert not worktree.path.exists()
    assert not worktree.gitdir.exists()
    bundles = list((tmp_path / "data" / "archives" / "orphans").glob("*.bundle"))
    assert bundles, "the clean orphan is bundled before deletion"


def test_an_empty_database_never_scans_the_default_root(tmp_path, monkeypatch):
    """空测试库不得回落到 settings.worktree_root。

    2026-11-05 事故：测试库为空时孤儿扫描回落到开发者的真实 ``wt/``，
    把生产目录当成孤儿清掉了。根只能来自本库的 workspace 行。
    """
    _archive_root(tmp_path, monkeypatch)
    monkeypatch.setattr("app.config.settings.worktree_root", tmp_path / "default-root")
    fixture = make_fixture(tmp_path)
    item = seed_and_claim(fixture)
    worktree = provision(fixture, task_id="T-1", attempt_id=item.attempt_id)
    with fixture.database.transaction() as conn:
        conn.execute("DELETE FROM workspaces")

    report = sweep_worktree_recycle(fixture.database, datetime.now(UTC), repo=fixture.repo)

    assert report.detail["orphans_removed"] == []
    assert report.detail["orphans_kept"] == []
    assert worktree.path.exists(), "an empty database owns no root, so nothing is scanned"


def test_roots_come_from_every_workspace_path_in_this_database(tmp_path, monkeypatch):
    """一个库可以有多个白名单 root（``wt/`` 与 ``data/fw/``），都要扫。"""
    _archive_root(tmp_path, monkeypatch)
    fixture = make_fixture(tmp_path)
    first = seed_and_claim(fixture, task_id="T-1")
    provision(fixture, task_id="T-1", attempt_id=first.attempt_id)
    other_root = tmp_path / "data" / "fw"
    other_manager = WorktreeManager(fixture.database, fixture.repo, root=other_root)
    second = seed_and_claim(fixture, task_id="T-2")
    other_manager.provision(
        task_id="T-2",
        attempt_id=second.attempt_id,
        group_short="g1",
        harness="harness-1",
        base_commit=base_commit(fixture),
    )
    third = seed_and_claim(fixture, task_id="T-3")
    orphan = other_manager.provision(
        task_id="T-3",
        attempt_id=third.attempt_id,
        group_short="g1",
        harness="harness-1",
        base_commit=base_commit(fixture),
    )
    with fixture.database.transaction() as conn:
        conn.execute("DELETE FROM workspaces WHERE attempt_id = ?", (third.attempt_id,))

    report = sweep_worktree_recycle(fixture.database, datetime.now(UTC), repo=fixture.repo)

    assert report.detail["orphans_removed"] == [str(orphan.path)]
    assert not orphan.path.exists()
    assert not orphan.gitdir.exists()


def test_empty_orphan_outside_the_allowlist_is_never_deleted(tmp_path, monkeypatch):
    r"""白名单外的空目录不删——即使本库有行指向那个 root。

    2026-11-06 事故的根因就是「按 workspace 行的父目录推导 root」：
    ``<tmp>\e2e-real-20261105\wt`` 与 ``.worktrees\codex\wt`` 因此被扫。
    这里放一个 M3 式的空仓库，证明默认扫描根本不会看它。
    """
    _archive_root(tmp_path, monkeypatch)
    fixture = make_fixture(tmp_path)
    outside_root = tmp_path / "outside" / "wt"
    outside_manager = WorktreeManager(fixture.database, fixture.repo, root=outside_root)
    live = seed_and_claim(fixture, task_id="T-1")
    live_tree = outside_manager.provision(
        task_id="T-1",
        attempt_id=live.attempt_id,
        group_short="g1",
        harness="harness-1",
        base_commit=base_commit(fixture),
    )
    stray = outside_root / "zzzz"
    stray.mkdir(parents=True)
    run_git("init", str(stray))

    report = sweep_worktree_recycle(fixture.database, datetime.now(UTC), repo=fixture.repo)

    assert report.detail["orphans_removed"] == []
    assert stray.exists(), "a root outside ORPHAN_ROOT_ALLOWLIST is never scanned"
    assert live_tree.path.exists()


def test_empty_orphan_inside_the_allowlist_is_still_cleaned(tmp_path, monkeypatch):
    """白名单内仍然要能清理，别为了安全把回收彻底废掉。"""
    _archive_root(tmp_path, monkeypatch)
    fixture = make_fixture(tmp_path)
    live = seed_and_claim(fixture, task_id="T-1")
    provision(fixture, task_id="T-1", attempt_id=live.attempt_id)
    stray = tmp_path / "wt" / "zzzz"
    stray.mkdir(parents=True)
    run_git("init", str(stray))

    report = sweep_worktree_recycle(fixture.database, datetime.now(UTC), repo=fixture.repo)

    assert report.detail["orphans_removed"] == [str(stray)]
    assert not stray.exists()
