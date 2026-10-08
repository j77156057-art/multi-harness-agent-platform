"""Buzz 桥：平台事件 → Buzz 频道（只写不读；ADR-DEV-005；buzz-bridge ⑤）。

形状与 :mod:`app.alerts` 同族：

* **游标**（`buzz_cursors`，stream = `buzz:<channel>`）**只在整批发布成功后
  推进**；条件 UPDATE（CAS）防并发双重推进。崩溃/断连后从旧游标续跑=至少
  一次，Nostr 事件 id = 内容哈希（`created_at` 取**源事件时间**，重放逐字节
  相同）→ relay 按 id 去重，显示效果近似恰好一次。
* **未配置 = 显式 no-op**（三项全空：不连 WS、exit 0）；**部分配置 = 拒绝**
  （改了一半就静默退化是运维最怕的形态，fail-closed 到 exit 2）。
* **死信停靠表不建**（对 ADR 决策 6 的实现取舍，卡内披露）：源=事件流本身即
  队列，"游标不动"就是重试；积压量在 `--check` 里可见（pending）。若未来
  需要"放弃积压"，另开卡，不在这里过度设计。

只写不读：本模块只 SELECT 平台库、只向 relay 发 AUTH/EVENT；不建订阅、不读
回 Buzz 的任何状态，Buzz 不可用不影响平台任何行为。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from app.buzz import client
from app.buzz.nostr import Signer
from app.buzz.projection import display_line, publish_events
from app.config import settings
from app.db.connection import Database
from app.events.store import EventRecord, EventStore


class BuzzConfigError(RuntimeError):
    """拒绝运行时的桥配置（部分配置/私钥文件缺失或非法）。消息不含密钥材料。"""


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _source_created_at(record_created_at: str) -> int:
    """Nostr created_at 取源事件时间：重放同一 batch 产出同 id（去重语义的前提）。"""
    return int(datetime.fromisoformat(record_created_at).timestamp())


@dataclass(frozen=True, slots=True)
class BuzzRunReport:
    configured: bool
    scanned: int
    detected: int
    published: int
    failed: int
    cursor: int
    pending: int
    error: str | None = None

    def as_json(self) -> dict[str, object]:
        return {
            "configured": self.configured,
            "scanned": self.scanned,
            "detected": self.detected,
            "published": self.published,
            "failed": self.failed,
            "cursor": self.cursor,
            "pending": self.pending,
            "error": self.error,
        }


class BuzzBridge:
    def __init__(
        self,
        database: Database,
        *,
        relay_url: str | None = None,
        channel: str | None = None,
        key_file: Path | None = None,
        timeout_seconds: float | None = None,
        max_per_run: int = 50,
    ) -> None:
        self.database = database
        self.relay_url = relay_url if relay_url is not None else settings.buzz_relay_ws
        self.channel = channel if channel is not None else settings.buzz_channel
        self.key_file = key_file if key_file is not None else settings.buzz_key_file
        self.timeout_seconds = (
            float(timeout_seconds)
            if timeout_seconds is not None
            else float(settings.buzz_timeout_seconds)
        )
        self.max_per_run = max_per_run

    # ------------------------------------------------------------ config

    def _resolve_config(self) -> tuple[str, str, Signer] | None:
        configured = [self.relay_url, self.channel, self.key_file]
        if all(item is None for item in configured):
            return None
        if not all(item is not None for item in configured):
            raise BuzzConfigError(
                "AP_BUZZ_RELAY_WS / AP_BUZZ_CHANNEL / AP_BUZZ_KEY_FILE must be set together"
            )
        try:
            signer = Signer.from_file(Path(self.key_file))
        except ValueError as exc:  # BuzzKeyError；消息不含密钥材料
            raise BuzzConfigError(str(exc)) from exc
        return str(self.relay_url), str(self.channel), signer

    # ---------------------------------------------------------------- runs

    def run_once(self) -> BuzzRunReport:
        config = self._resolve_config()
        if config is None:
            return BuzzRunReport(
                configured=False,
                scanned=0,
                detected=0,
                published=0,
                failed=0,
                cursor=0,
                pending=0,
            )
        relay_url, channel, signer = config
        stream = f"buzz:{channel}"
        cursor = self._ensure_cursor(stream)
        rows = self.database.read(
            "SELECT * FROM task_events WHERE seq > ? ORDER BY seq ASC LIMIT ?",
            (cursor, self.max_per_run),
        )
        if not rows:
            return self._report(0, 0, 0, 0, cursor, stream, None)
        store = EventStore(self.database)
        # 复用 store 的行解析：顺带校验 payload 哈希，坏事件不进消息
        records = [store._from_row(row) for row in rows]
        upper = records[-1].seq
        wanted = publish_events()
        candidates = [record for record in records if record.event_name in wanted]
        if not candidates:
            self._advance(stream, cursor, upper)
            return self._report(len(rows), 0, 0, 0, upper, stream, None)
        groups = self._group_map(candidates)
        events = [
            signer.channel_message(
                channel=channel,
                content=display_line(record, group_id=groups.get(record.aggregate_id)),
                created_at=_source_created_at(record.created_at),
            )
            for record in candidates
        ]
        try:
            client.publish_events(relay_url, signer, events, timeout=self.timeout_seconds)
        except client.BuzzRelayError as exc:
            return self._report(
                len(rows), len(candidates), 0, len(candidates), cursor, stream, str(exc)
            )
        self._advance(stream, cursor, upper)
        return self._report(len(rows), len(candidates), len(candidates), 0, upper, stream, None)

    def check(self) -> tuple[bool, dict[str, object]]:
        """`--check` 的实现：配置三态 + 游标 + 积压 + next 候选。"""
        try:
            config = self._resolve_config()
        except BuzzConfigError as exc:
            return False, {"configured": False, "ok": False, "reason": str(exc)}
        if config is None:
            return True, {"configured": False, "ok": True, "reason": None}
        _, channel, _ = config
        stream = f"buzz:{channel}"
        cursor = self._read_cursor(stream)
        return True, {
            "configured": True,
            "ok": True,
            "relay": self.relay_url,
            "channel": channel,
            "key_file": str(self.key_file),
            "cursor": cursor,
            "pending": self._pending(stream, cursor),
            "next": self._next_candidates(cursor),
        }

    # ------------------------------------------------------------- cursor

    def _ensure_cursor(self, stream: str) -> int:
        with self.database.transaction() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO buzz_cursors(stream, cursor_seq, updated_at)"
                " VALUES (?, 0, ?)",
                (stream, _now()),
            )
            row = conn.execute(
                "SELECT cursor_seq FROM buzz_cursors WHERE stream = ?", (stream,)
            ).fetchone()
        return int(row["cursor_seq"])

    def _read_cursor(self, stream: str) -> int:
        rows = self.database.read("SELECT cursor_seq FROM buzz_cursors WHERE stream = ?", (stream,))
        return int(rows[0]["cursor_seq"]) if rows else 0

    def _advance(self, stream: str, from_cursor: int, to_cursor: int) -> None:
        # CAS：rowcount 0 = 另一个桥先推进了；两边都是"至少一次+按 id 去重"，安全
        with self.database.transaction() as conn:
            conn.execute(
                "UPDATE buzz_cursors SET cursor_seq = ?, updated_at = ?"
                " WHERE stream = ? AND cursor_seq = ?",
                (to_cursor, _now(), stream, from_cursor),
            )

    # ------------------------------------------------------------- lookups

    def _pending(self, stream: str, cursor: int) -> int:
        names = sorted(publish_events())
        placeholders = ",".join("?" for _ in names)
        rows = self.database.read(
            f"SELECT COUNT(*) AS n FROM task_events WHERE seq > ? AND event_name IN ({placeholders})",
            (cursor, *names),
        )
        return int(rows[0]["n"])

    def _next_candidates(self, cursor: int) -> list[dict[str, object]]:
        names = sorted(publish_events())
        placeholders = ",".join("?" for _ in names)
        rows = self.database.read(
            f"SELECT seq, event_name, aggregate_id FROM task_events"
            f" WHERE seq > ? AND event_name IN ({placeholders}) ORDER BY seq ASC LIMIT 5",
            (cursor, *names),
        )
        return [
            {
                "seq": int(row["seq"]),
                "event_name": str(row["event_name"]),
                "task_id": str(row["aggregate_id"]),
            }
            for row in rows
        ]

    def _group_map(self, records: list[EventRecord]) -> dict[str, str]:
        task_ids = sorted({r.aggregate_id for r in records if r.aggregate_type == "task"})
        if not task_ids:
            return {}
        placeholders = ",".join("?" for _ in task_ids)
        rows = self.database.read(
            f"SELECT task_id, group_id FROM tasks WHERE task_id IN ({placeholders})",
            tuple(task_ids),
        )
        return {str(row["task_id"]): str(row["group_id"]) for row in rows}

    # ------------------------------------------------------------- helpers

    def _report(
        self,
        scanned: int,
        detected: int,
        published: int,
        failed: int,
        cursor: int,
        stream: str,
        error: str | None,
    ) -> BuzzRunReport:
        return BuzzRunReport(
            configured=True,
            scanned=scanned,
            detected=detected,
            published=published,
            failed=failed,
            cursor=cursor,
            pending=self._pending(stream, cursor),
            error=error,
        )
