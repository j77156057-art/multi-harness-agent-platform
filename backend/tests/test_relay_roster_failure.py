"""名册读不出来的时候，邮局**不该**把信退回。

以前 `_roster_from_db` 读失败返回空集合，`known_recipients()` 还把它缓存住
——于是「读不到」被当成「没有任何窗口」，每一封信都因为「作者不在名册」被退回。
信被白白退回，人还得重写一遍，而真正的原因（平台暂时读不到名册）没人看见。

现在：读失败抛 `RosterUnavailable` → 这一轮投递停下并报原因，信**留在发件箱里**
等下一轮。
"""

from __future__ import annotations

from pathlib import Path

from app.relay.mailbox import Mailbox
from app.relay.worker import RelayWorker, RosterUnavailable


class FakeClient:
    def __init__(self) -> None:
        self.posts: list[dict] = []

    def get(self, path: str):
        return {"harnesses": []}

    def post(self, path: str, json: dict):
        self.posts.append({"path": path, "json": json})
        return {"ok": True}


def _worker(tmp_path: Path, roster) -> tuple[RelayWorker, FakeClient, Mailbox]:
    mailbox = Mailbox(root=tmp_path / "relay")
    mailbox.ensure()
    client = FakeClient()
    worker = RelayWorker(
        mailbox=mailbox,
        client=client,
        group="G-1",
        admins=frozenset({"codex-window-admin"}),
        roster=roster,
    )
    return worker, client, mailbox


def test_a_letter_stays_in_the_outbox_when_the_roster_cannot_be_read(tmp_path):
    def broken_roster():
        raise RosterUnavailable("读不了窗口名册（state.db）：database is locked")

    worker, client, mailbox = _worker(tmp_path, broken_roster)
    letter = mailbox.outbox / "codex-1.md"
    letter.write_text("来自 codex\n\n给 opencode：\n看一眼这个\n", encoding="utf-8")

    results = worker.sweep()

    assert results and results[0]["status"] == "deferred"
    assert "名册" in str(results[0].get("reason") or "")
    # 信还在发件箱里（没被退回，也没被投出去）。
    assert letter.exists()
    assert not list(mailbox.returned.glob("*.md"))
    assert client.posts == []


def test_the_failed_read_is_not_cached(tmp_path):
    """读失败不能被缓存成空名册：下一轮读通了，信就照常能投。"""
    state = {"fail": True}

    def roster():
        if state["fail"]:
            raise RosterUnavailable("读不了窗口名册")
        return {"codex", "opencode"}

    worker, client, mailbox = _worker(tmp_path, roster)
    letter = mailbox.outbox / "codex-1.md"
    letter.write_text("来自 codex\n\n给 opencode：\n看一眼这个\n", encoding="utf-8")

    assert worker.sweep()[0]["status"] == "deferred"
    state["fail"] = False
    assert worker.sweep()[0]["status"] == "delivered"
