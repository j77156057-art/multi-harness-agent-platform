"""Scripted acceptance tests for pool self-provision discovery."""

from __future__ import annotations

from typing import Any

from app.pool.provision import ALERT_KIND, candidates, check


class FakeAlerts:
    def __init__(self) -> None:
        self.seen: set[tuple[str, str]] = set()
        self.payloads: list[dict[str, Any]] = []

    def enqueue(self, *, kind: str, payload: dict[str, Any], source_key: str, **_: Any) -> bool:
        key = (kind, source_key)
        if key in self.seen:
            return False
        self.seen.add(key)
        self.payloads.append(payload)
        return True


def _rows() -> list[dict[str, Any]]:
    return [
        {"harness_id": "known", "provider": "tencent", "adapter": "codebuddy", "status": "online"},
        {
            "harness_id": "new",
            "provider": "new-provider",
            "adapter": "new-cli",
            "status": "online",
            "registration_version": "2",
        },
    ]


def test_uncovered_harness_is_candidate_but_never_assumed_free():
    found = candidates(
        _rows(), [{"name": "codebuddy-cli", "binary": "codebuddy", "model": "hy3", "tier": "free"}]
    )
    item = next(item for item in found if item["kind"] == "uncovered_harness")
    assert item["harness_id"] == "new"
    assert item["tier"] == "unknown"
    assert item["evidence"]["source"] == "harnesses registry"


def test_missing_tier_is_explicitly_reported_and_not_free():
    found = candidates([], [{"name": "new-cli", "binary": "new-cli", "model": "m1"}])
    assert found == [
        {
            "kind": "tier_missing",
            "slot": "new-cli",
            "model": "m1",
            "tier": "unknown",
            "reason": "declared slot has no explicit free/reserve/metered tier",
            "evidence": {"source": "pool cli_slots declaration"},
        }
    ]


def test_unknown_cli_is_never_executed():
    found = candidates(_rows(), [], declarations={"new-cli": object()})
    assert {item["harness_id"] for item in found if item["kind"] == "uncovered_harness"} == {"new"}


def test_first_snapshot_then_change_is_deduplicated(tmp_path):
    alerts = FakeAlerts()
    path = tmp_path / "candidates.json"
    slots = [{"name": "codebuddy-cli", "binary": "codebuddy", "model": "hy3", "tier": "free"}]
    first = check(_rows()[:1], slots, alerts, snapshot_path=path)
    assert first.first_snapshot and first.alerts_enqueued == 0
    second = check(_rows(), slots, alerts, snapshot_path=path)
    assert second.changed and second.alerts_enqueued == 1
    assert alerts.payloads[0]["current"][0]["evidence"]["source"] == "harnesses registry"
    replay = check(_rows(), slots, alerts, snapshot_path=path)
    assert not replay.changed
    assert len(alerts.seen) == 1 and next(iter(alerts.seen))[0] == ALERT_KIND
