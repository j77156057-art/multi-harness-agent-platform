"""Scripted tests for the free-set watch: no live pool, no real probing.

The acceptance rounds from the proposal (same roster stays silent, each
change pages once with old/new rosters, a repeated roster never double-pages)
plus the first-run baseline rule: no prior snapshot means "nothing to diff",
no alert, honest first_snapshot flag.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from app.pool.free_set_watch import ALERT_KIND, check

SNAPSHOT = Path("free-set.json")


class FakePool:
    """``tier``-labelled catalogue entries, recomposable between rounds."""

    def __init__(self, entries: list[dict[str, Any]]) -> None:
        self.entries = entries

    def models(self, *, refresh: bool = False) -> list[dict[str, Any]]:
        self.refreshed = refresh
        return self.entries


class FakeAlerts:
    """Records enqueues; replays the alert table's UNIQUE(kind, source_key)."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []
        self.seen: set[tuple[str, str]] = set()
        self.payloads: dict[str, dict[str, Any]] = {}

    def enqueue(
        self,
        *,
        kind: str,
        payload: dict[str, Any],
        task_id: str | None = None,
        source_seq: int | None = None,
        source_key: str | None = None,
    ) -> bool:
        assert kind and source_key and (source_seq is None or kind != ALERT_KIND)
        key = (kind, source_key)
        self.calls.append(key)
        if key in self.seen:
            return False  # the table refuses a second row with the same key
        self.seen.add(key)
        self.payloads[source_key] = payload
        return True


def _catalogue(*pairs: tuple[str, str]) -> FakePool:
    return FakePool([{"id": name, "tier": tier} for name, tier in pairs])


def test_first_run_writes_a_baseline_and_pages_nobody(tmp_path):
    pool = _catalogue(("free/a", "free"), ("metered/x", "metered"))
    report = check(pool, FakeAlerts(), snapshot_path=tmp_path / SNAPSHOT)
    assert report.first_snapshot is True and report.changed is False
    assert report.alerts_enqueued == 0, "没有基线就没有 diff，首跑不喊人"
    assert report.current == ["free/a"], "只收 free 档，metered 不入镜"
    saved = json.loads((tmp_path / SNAPSHOT).read_text(encoding="utf-8"))
    assert saved["names"] == ["free/a"]


def test_the_same_roster_is_silent_and_the_baseline_is_untouched(tmp_path):
    pool = _catalogue(("free/a", "free"), ("free/b", "free"))
    path = tmp_path / SNAPSHOT
    first = check(pool, FakeAlerts(), snapshot_path=path)
    before = path.read_text(encoding="utf-8")
    second = check(pool, FakeAlerts(), snapshot_path=path)
    assert first.changed is False and second.changed is False
    assert second.first_snapshot is False
    assert path.read_text(encoding="utf-8") == before, "没变就不重写账本"


def test_each_roster_change_pages_once_with_the_old_and_new_roster(tmp_path):
    path = tmp_path / SNAPSHOT
    alerts = FakeAlerts()
    # round 1: baseline
    check(_catalogue(("free/a", "free"), ("free/b", "free")), alerts, snapshot_path=path)
    # round 2: one model leaves
    report = check(_catalogue(("free/a", "free")), alerts, snapshot_path=path)
    assert report.changed is True and report.alerts_enqueued == 1
    payload = alerts.payloads[report.fingerprint]
    assert payload["previous"] == ["free/a", "free/b"]
    assert payload["current"] == ["free/a"]
    assert payload["removed"] == ["free/b"] and payload["added"] == []
    # round 3: a new free model appears
    report = check(_catalogue(("free/a", "free"), ("free/c", "free")), alerts, snapshot_path=path)
    assert report.changed is True and report.alerts_enqueued == 1
    payload = alerts.payloads[report.fingerprint]
    assert payload["added"] == ["free/c"] and payload["previous"] == ["free/a"]
    # round 4: back to the first roster (a flap) -- it pages again: each state
    # genuinely deserves a fresh look, and report 2 already covered the middle
    report = check(_catalogue(("free/a", "free/b")), alerts, snapshot_path=path)
    assert report.changed is True and report.alerts_enqueued == 1
    assert len(alerts.calls) == 3
    assert {kind for kind, _ in alerts.calls} == {ALERT_KIND}


def test_a_repeated_roster_never_double_pages(tmp_path):
    path = tmp_path / SNAPSHOT
    alerts = FakeAlerts()
    baseline = _catalogue(("free/a", "free"))
    check(baseline, alerts, snapshot_path=path)
    changed = _catalogue(("free/a", "free"), ("free/b", "free"))
    first = check(changed, alerts, snapshot_path=path)
    replay = check(changed, alerts, snapshot_path=path)
    assert first.changed is True and first.alerts_enqueued == 1
    assert replay.changed is False, "同一名单第二轮就是没变，不喊也不投"
    # The dedupe at the table level: roll the snapshot file back (an operator
    # restored an old one), so the SAME A→B transition is re-diffed — the
    # table's fingerprint key refuses the second row.
    import hashlib

    baseline_fp = hashlib.sha256(b"free/a\n").hexdigest()
    path.write_text(
        json.dumps({"names": ["free/a"], "fingerprint": baseline_fp}, ensure_ascii=False),
        encoding="utf-8",
    )
    refire = check(changed, alerts, snapshot_path=path)
    assert refire.changed is True and refire.alerts_enqueued == 0, "已投过的不重投"
    # and returning to the baseline is a change again -- with its own key
    report = check(baseline, alerts, snapshot_path=path)
    assert report.changed is True and report.alerts_enqueued == 1
    assert len({key for _, key in alerts.calls}) == 2, "两变迁两键，无重键"


@pytest.mark.parametrize("corrupt", [b"", b"{not json"])
def test_a_corrupt_snapshot_counts_as_lost_baseline_not_a_diff(tmp_path, corrupt):
    path = tmp_path / SNAPSHOT
    path.write_bytes(corrupt)
    alerts = FakeAlerts()
    report = check(_catalogue(("free/a", "free")), alerts, snapshot_path=path)
    assert report.first_snapshot is True and report.alerts_enqueued == 0


def test_the_watch_can_force_a_catalogue_refresh(tmp_path):
    pool = _catalogue(("free/a", "free"))
    check(pool, FakeAlerts(), snapshot_path=tmp_path / SNAPSHOT, refresh=True)
    assert pool.refreshed is True, "轮询式调用可以点名刷新，冷却式是默认缓存"
