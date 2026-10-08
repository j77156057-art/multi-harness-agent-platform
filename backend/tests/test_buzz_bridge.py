"""Buzz 单向桥：NIP-01/42 事件、投影白名单、游标语义、真 WS 端到端（INTEG-BUZZ-001）。

测试内自建最小 relay（websockets 服务端，**真 WS 真握手**）：连接即发 NIP-42
AUTH challenge、强制先 AUTH 再 EVENT、重算事件 id + schnorr 验签
（PublicKeyXOnly）+ `h` tag 校验、按 event id 去重、回 OK。桥不是 mock 掉的：
DB → 投影 → 真网络栈 → relay 全链。

BIP340 官方向量 #0（bip-0340/test-vectors.csv 第 0 行）逐字节固化本仓签名路径。
"""

from __future__ import annotations

import asyncio
import json
import socket
import threading
from pathlib import Path

import pytest
from websockets.asyncio.client import connect
from websockets.asyncio.server import serve
from websockets.exceptions import ConnectionClosed

from app.buzz import __main__ as buzz_cli
from app.buzz import nostr
from app.buzz.bridge import BuzzBridge
from app.buzz.nostr import Signer
from app.buzz.projection import (
    display_line,
    migration_events,
    next_state,
    observable_events,
    publish_events,
    recovery_events,
)
from app.config import settings
from app.events.store import EventRecord, EventStore
from tests.claim_support import make_database, seed_project, seed_task

CHANNEL = "chan-buzz-1"
KEY_HEX = "11" * 32
LEAK = "SECRET-XYZ-DO-NOT-LEAK"

# BIP340 官方向量 #0
VECTOR0_SK = "0000000000000000000000000000000000000000000000000000000000000003"
VECTOR0_PUB = "F9308A019258C31049344F85F89D5229B531C845836F99B08601F113BCE036F9"
VECTOR0_SIG = (
    "E907831F80848D1069A5371B402410364BDF1C5F8307B0084C55F1CE2DCA821525F66A4A85EA8B71"
    "E482A74F382D2CE5EBEEE8FDB2172F477DF4900D310536C0"
)


# ---------------------------------------------------------------- test relay


class RecordingRelay:
    """最小 NIP-01/NIP-42 relay：只理解 AUTH/EVENT/OK，够验桥的全部行为。"""

    def __init__(
        self,
        *,
        channel: str,
        challenge: str = "challenge-0001",
        require_auth: bool = True,
        reject_events: bool = False,
    ) -> None:
        self.channel = channel
        self.challenge = challenge
        self.require_auth = require_auth
        self.reject_events = reject_events
        self.url: str = ""
        self.auths: list[dict] = []
        self.messages: list[dict] = []
        self.seen_ids: set[str] = set()
        self.duplicate_deliveries = 0
        self._loop: asyncio.AbstractEventLoop | None = None
        self._stop: asyncio.Event | None = None
        self._ready = threading.Event()
        self._thread: threading.Thread | None = None

    # -- lifecycle ---------------------------------------------------------

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        assert self._ready.wait(timeout=10), "relay did not start"

    def stop(self) -> None:
        if self._loop is not None and self._stop is not None:
            self._loop.call_soon_threadsafe(self._stop.set)
        if self._thread is not None:
            self._thread.join(timeout=10)

    def _run(self) -> None:
        asyncio.run(self._serve())

    async def _serve(self) -> None:
        self._loop = asyncio.get_running_loop()
        self._stop = asyncio.Event()
        async with serve(self._handler, "127.0.0.1", 0) as server:
            port = server.sockets[0].getsockname()[1]
            self.url = f"ws://127.0.0.1:{port}"
            self._ready.set()
            await self._stop.wait()

    # -- protocol ----------------------------------------------------------

    @staticmethod
    def _tag_value(event: dict, name: str) -> str | None:
        for tag in event.get("tags", []):
            if len(tag) >= 2 and tag[0] == name:
                return str(tag[1])
        return None

    async def _handler(self, ws) -> None:
        await ws.send(json.dumps(["AUTH", self.challenge]))
        authed = not self.require_auth
        try:
            async for raw in ws:
                frame = json.loads(raw)
                if frame[0] == "AUTH":
                    event = frame[1]
                    ok = (
                        int(event["kind"]) == nostr.KIND_AUTH
                        and self._tag_value(event, "relay") == self.url
                        and self._tag_value(event, "challenge") == self.challenge
                        and nostr.verify_event(event)
                    )
                    if ok:
                        self.auths.append(event)
                        authed = True
                    await ws.send(json.dumps(["OK", event["id"], ok, "" if ok else "bad auth"]))
                elif frame[0] == "EVENT":
                    event = frame[1]
                    if self.require_auth and not authed:
                        await ws.send(json.dumps(["OK", event["id"], False, "auth-required"]))
                        continue
                    if self.reject_events:
                        await ws.send(json.dumps(["OK", event["id"], False, "blocked by policy"]))
                        continue
                    valid = (
                        int(event["kind"]) == nostr.KIND_CHANNEL_MESSAGE
                        and nostr.verify_event(event)
                        and self._tag_value(event, "h") == self.channel
                    )
                    if valid:
                        if event["id"] in self.seen_ids:
                            self.duplicate_deliveries += 1
                        else:
                            self.seen_ids.add(event["id"])
                            self.messages.append(event)
                    await ws.send(
                        json.dumps(["OK", event["id"], valid, "" if valid else "invalid event"])
                    )
                else:
                    # 只写不读的副产品：桥不该发 REQ/CLOSE；发来就回 NOTICE 留痕
                    await ws.send(json.dumps(["NOTICE", f"unsupported frame: {frame[0]}"]))
        except ConnectionClosed:
            pass


@pytest.fixture()
def relay():
    server = RecordingRelay(channel=CHANNEL)
    server.start()
    try:
        yield server
    finally:
        server.stop()


# ---------------------------------------------------------------- helpers


def _write_key(tmp_path: Path) -> Path:
    path = tmp_path / "buzz-key.hex"
    path.write_text(KEY_HEX + "\n", encoding="utf-8")
    return path


def _bridge(database, url: str, key_file: Path, **overrides) -> BuzzBridge:
    return BuzzBridge(
        database,
        relay_url=url,
        channel=CHANNEL,
        key_file=key_file,
        timeout_seconds=5.0,
        **overrides,
    )


def _seed(database) -> None:
    seed_project(database)
    seed_task(database, task_id="T-1", group_id="G-1")
    store = EventStore(database)
    store.append(
        event_name="plan.approve",
        aggregate_type="task",
        aggregate_id="T-1",
        actor_type="human",
        actor_id="operator-1",
        payload={"decided_by": "operator-1", "note": LEAK},
    )
    store.append(
        event_name="claim",
        aggregate_type="task",
        aggregate_id="T-1",
        actor_type="harness",
        actor_id="harness-1",
        payload={"attempt_id": "att-1"},
    )
    store.append(
        event_name="human.question",
        aggregate_type="task",
        aggregate_id="T-1",
        actor_type="harness",
        actor_id="harness-1",
        payload={"question": LEAK},
    )
    # O 集合：默认静默
    store.append(
        event_name="idempotency_conflict",
        aggregate_type="task",
        aggregate_id="T-1",
        actor_type="scheduler",
        actor_id="idempotency",
        payload={"conflict": LEAK},
    )
    # M 集合但属退避提醒：默认静默
    store.append(
        event_name="escalation_reminder",
        aggregate_type="task",
        aggregate_id="T-1",
        actor_type="scheduler",
        actor_id="sweeper",
        payload={"reminded": 1},
    )
    # O 集合的安全类：ADR 点名放行
    store.append(
        event_name="secret_scan_blocked",
        aggregate_type="task",
        aggregate_id="T-1",
        actor_type="scheduler",
        actor_id="scanner",
        payload={"pattern": LEAK},
    )


EXPECTED_PUBLISHED = ["plan.approve", "claim", "human.question", "secret_scan_blocked"]


def _dead_url() -> str:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    return f"ws://127.0.0.1:{port}"


# ---------------------------------------------------------------- crypto


def test_bip340_vector_zero_byte_for_byte():
    signer = Signer.from_hex(VECTOR0_SK)
    assert signer.pubkey.upper() == VECTOR0_PUB
    # 向量 msg = 32 字节零；本仓签名路径（零 aux）必须逐字节一致
    signature = signer.sign_digest(bytes(32)).hex().upper()
    assert signature == VECTOR0_SIG


def test_event_id_recompute_and_tamper_detection():
    signer = Signer.from_hex(KEY_HEX)
    event = signer.channel_message(channel=CHANNEL, content="hello", created_at=1_700_000_000)
    payload = event.as_dict()
    assert payload["id"] == nostr.event_id(
        payload["pubkey"], payload["created_at"], payload["kind"], event.tags, payload["content"]
    )
    assert nostr.verify_event(payload)
    tampered = dict(payload, content="hello!")
    assert not nostr.verify_event(tampered)


def test_key_file_rejects_non_hex_without_echoing_content(tmp_path):
    path = tmp_path / "bad.hex"
    path.write_text("deadbeef-oops", encoding="utf-8")
    with pytest.raises(ValueError) as err:
        Signer.from_file(path)
    assert "64-char hex" in str(err.value)
    assert "deadbeef-oops" not in str(err.value)


# ---------------------------------------------------------------- projection


def test_publish_set_is_derived_from_the_frozen_allowlist():
    migration = migration_events()
    observable = observable_events()
    recovery = recovery_events()
    published = publish_events()
    # §37.7 冻结计数（M/O/R = 74/14/1，并集 89）
    assert (len(migration), len(observable), len(recovery)) == (74, 14, 1)
    assert len(migration | observable | recovery) == 89
    # 发布集 = M − {escalation_reminder} + {permission_denied, secret_scan_blocked}
    assert migration - published == {"escalation_reminder"}
    assert published - migration == {"permission_denied", "secret_scan_blocked"}
    assert published & recovery == set()
    assert published <= (migration | observable)
    # human.* 全集在发布集内
    assert {name for name in migration if name.startswith("human")} <= published
    # 迁移后状态可推导且无歧义（多值/缺项在 _event_next_map 内直接 raise）
    assert {name for name in migration if next_state(name) is None} == set()
    assert next_state("permission_denied") is None


def test_display_line_renders_whitelist_fields_only():
    record = EventRecord(
        seq=1,
        event_name="claim",
        aggregate_type="task",
        aggregate_id="T-1",
        actor_type="harness",
        actor_id="h-1",
        payload={"attempt": "att-1"},
        payload_hash="unused",
        schema_version=1,
        created_at="2026-10-09T03:10:22.123456+00:00",
    )
    line = display_line(record, group_id="G-1")
    assert (
        line == "[2026-10-09 03:10:22 UTC] · claim · task:T-1 · group G-1 · harness h-1 → claimed"
    )
    assert "att-1" not in line


# ---------------------------------------------------------------- end to end


def test_round_trip_publishes_projected_messages_in_seq_order(relay, tmp_path):
    database = make_database(tmp_path)
    _seed(database)
    bridge = _bridge(database, relay.url, _write_key(tmp_path))

    report = bridge.run_once()

    assert report.configured and report.failed == 0
    assert report.detected == len(EXPECTED_PUBLISHED)
    assert report.published == len(EXPECTED_PUBLISHED)
    assert report.pending == 0
    contents = [message["content"] for message in relay.messages]
    assert len(contents) == len(EXPECTED_PUBLISHED)
    for expected_name, content in zip(EXPECTED_PUBLISHED, contents, strict=True):
        assert expected_name in content
    for message in relay.messages:
        assert int(message["kind"]) == nostr.KIND_CHANNEL_MESSAGE
        assert ["h", CHANNEL] in message["tags"]
        assert nostr.verify_event(message)
    # 群信息来自 tasks 表关联；payload 一个字节都没有出境
    assert all("group G-1" in content for content in contents)
    assert all(LEAK not in content for content in contents)
    assert "→ claimed" in contents[1]


def test_cursor_advances_and_second_round_is_a_noop(relay, tmp_path):
    database = make_database(tmp_path)
    _seed(database)
    bridge = _bridge(database, relay.url, _write_key(tmp_path))

    first = bridge.run_once()
    second = bridge.run_once()

    assert first.published == len(EXPECTED_PUBLISHED)
    assert second.published == 0 and second.detected == 0 and second.scanned == 0
    assert second.cursor == first.cursor
    assert len(relay.messages) == len(EXPECTED_PUBLISHED)
    assert relay.duplicate_deliveries == 0


def test_relay_down_keeps_cursor_then_retry_dedupes_by_event_id(relay, tmp_path):
    database = make_database(tmp_path)
    _seed(database)
    key = _write_key(tmp_path)

    down = _bridge(database, _dead_url(), key)
    failed = down.run_once()
    assert failed.configured and failed.published == 0
    assert failed.failed == len(EXPECTED_PUBLISHED)
    assert failed.error is not None
    assert failed.cursor == 0
    assert failed.pending == len(EXPECTED_PUBLISHED)

    live = _bridge(database, relay.url, key)
    retried = live.run_once()
    assert retried.published == len(EXPECTED_PUBLISHED)
    ids = [message["id"] for message in relay.messages]

    # 至少一次：把游标倒回 0 重放同一批 → 同 id（源事件时间锁定的确定性签名）
    with database.transaction() as conn:
        conn.execute("UPDATE buzz_cursors SET cursor_seq = 0")
    replay = live.run_once()
    assert replay.published == len(EXPECTED_PUBLISHED)
    assert [message["id"] for message in relay.messages] == ids
    assert relay.duplicate_deliveries == len(EXPECTED_PUBLISHED)


def test_relay_policy_rejection_keeps_cursor(tmp_path):
    rejecting = RecordingRelay(channel=CHANNEL, reject_events=True)
    rejecting.start()
    try:
        database = make_database(tmp_path)
        _seed(database)
        bridge = _bridge(database, rejecting.url, _write_key(tmp_path))
        report = bridge.run_once()
        assert report.failed == len(EXPECTED_PUBLISHED)
        assert "rejected event" in str(report.error)
        assert report.cursor == 0
        assert rejecting.messages == []
    finally:
        rejecting.stop()


def test_auth_is_sent_first_and_matches_the_challenge(relay, tmp_path):
    database = make_database(tmp_path)
    _seed(database)
    bridge = _bridge(database, relay.url, _write_key(tmp_path))

    bridge.run_once()

    assert len(relay.auths) == 1
    auth = relay.auths[0]
    assert int(auth["kind"]) == nostr.KIND_AUTH
    tags = {tag[0]: tag[1] for tag in auth["tags"]}
    assert tags["relay"] == relay.url
    assert tags["challenge"] == relay.challenge
    assert nostr.verify_event(auth)


def test_relay_refuses_events_without_auth(relay):
    """relay 侧强制 AUTH 是真的（对照组）：未认证的 EVENT 被拒且不落账。"""

    async def raw_publish() -> tuple[bool, str]:
        signer = Signer.from_hex(KEY_HEX)
        event = signer.channel_message(channel=CHANNEL, content="sneaky", created_at=1_700_000_000)
        async with connect(relay.url) as ws:
            await ws.recv()  # AUTH challenge 故意不理
            await ws.send(json.dumps(["EVENT", event.as_dict()]))
            frame = json.loads(await ws.recv())
            return bool(frame[2]), str(frame[3])

    accepted, detail = asyncio.run(raw_publish())
    assert accepted is False
    assert detail == "auth-required"
    assert relay.messages == []


# ---------------------------------------------------------------- CLI


def test_cli_unconfigured_is_an_explicit_noop(monkeypatch, capsys, tmp_path):
    database = make_database(tmp_path)
    monkeypatch.setattr(settings, "db_path", database.path)
    monkeypatch.setattr(settings, "buzz_relay_ws", None)
    monkeypatch.setattr(settings, "buzz_channel", None)
    monkeypatch.setattr(settings, "buzz_key_file", None)

    code = buzz_cli.main(["--once"])

    assert code == 0
    payload = json.loads(capsys.readouterr().out.strip())
    assert payload["configured"] is False and payload["published"] == 0


def test_cli_partial_config_refuses(monkeypatch, capsys, tmp_path):
    database = make_database(tmp_path)
    monkeypatch.setattr(settings, "db_path", database.path)
    monkeypatch.setattr(settings, "buzz_relay_ws", "ws://127.0.0.1:9")
    monkeypatch.setattr(settings, "buzz_channel", None)
    monkeypatch.setattr(settings, "buzz_key_file", None)

    assert buzz_cli.main(["--check"]) == 2
    payload = json.loads(capsys.readouterr().out.strip())
    assert payload["ok"] is False
    assert "AP_BUZZ_KEY_FILE" in payload["reason"]

    assert buzz_cli.main(["--once"]) == 2


def test_cli_missing_key_file_refuses(monkeypatch, tmp_path):
    database = make_database(tmp_path)
    monkeypatch.setattr(settings, "db_path", database.path)
    monkeypatch.setattr(settings, "buzz_relay_ws", "ws://127.0.0.1:9")
    monkeypatch.setattr(settings, "buzz_channel", CHANNEL)
    monkeypatch.setattr(settings, "buzz_key_file", tmp_path / "absent.hex")

    assert buzz_cli.main(["--check"]) == 2
    assert buzz_cli.main(["--once"]) == 2


def test_cli_check_reports_cursor_and_next_candidates(monkeypatch, capsys, relay, tmp_path):
    database = make_database(tmp_path)
    _seed(database)
    monkeypatch.setattr(settings, "db_path", database.path)
    monkeypatch.setattr(settings, "buzz_relay_ws", relay.url)
    monkeypatch.setattr(settings, "buzz_channel", CHANNEL)
    monkeypatch.setattr(settings, "buzz_key_file", _write_key(tmp_path))

    assert buzz_cli.main(["--check"]) == 0
    payload = json.loads(capsys.readouterr().out.strip())
    assert payload["ok"] is True and payload["configured"] is True
    assert payload["cursor"] == 0
    assert payload["pending"] == len(EXPECTED_PUBLISHED)
    assert [item["event_name"] for item in payload["next"]] == EXPECTED_PUBLISHED

    assert buzz_cli.main(["--once"]) == 0
    run = json.loads(capsys.readouterr().out.strip())
    assert run["published"] == len(EXPECTED_PUBLISHED)
    assert len(relay.messages) == len(EXPECTED_PUBLISHED)


def test_cli_returns_one_when_the_relay_is_down(monkeypatch, capsys, tmp_path):
    database = make_database(tmp_path)
    _seed(database)
    monkeypatch.setattr(settings, "db_path", database.path)
    monkeypatch.setattr(settings, "buzz_relay_ws", _dead_url())
    monkeypatch.setattr(settings, "buzz_channel", CHANNEL)
    monkeypatch.setattr(settings, "buzz_key_file", _write_key(tmp_path))

    assert buzz_cli.main(["--once"]) == 1
