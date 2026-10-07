"""出站告警：订阅事件流、HMAC 签名投递、失败重试直至停靠（alert-webhook）。

机制学习自 Paperclip 的出站 webhook 模式（签名载荷、仅 HTTPS、可重试投递，
见 `docs/reports/paperclip-buzz-source-review.md` §2.8），**代码为本仓原生**：
游标走 `task_events`、投递队列走 `alert_deliveries`、HTTP 走标准库——没有
复制任何被审查项目的代码，因此也不需要第三方许可记账。

设计要点：

* **订阅集合由冻结表推导**：`default_transition_table()` 里 `next is ESCALATED`
  的边——升级事件全集（stuck/aging/block/verify/review/merge/budget 各族），
  表变则集合变，不手抄、不腐化。
* **异步后置**（R10 §15 的纪律）：检测只读事件流 + 入队；投递独立一步。任何
  一步失败都不回写权威状态。
* **入队幂等**：`UNIQUE(kind, source_seq)`——同一源事件重复扫描只产生一条投递；
  游标推进用条件 UPDATE，崩溃后从旧游标续跑只会重复入队被去重，不会丢失。
* **投递语义**：2xx=delivered；其余（HTTP 错误、超时、网络失败）=attempts+1
  并记录 last_error；达 `max_attempts` 后停靠为 `failed`（不再自动重试，
  行本身可被操作面观察）。
* **安全底线**：目标 URL 仅 https，或 loopback 的 http（本机联调到真服务）；
  签名密钥缺失时拒绝运行（没有密钥的 HMAC 是自欺）；不跟随重定向
  （避免签名载荷被转送到未预期的主机）。
"""

from __future__ import annotations

import hashlib
import hmac
import json
import sqlite3
import urllib.error
import urllib.parse
import urllib.request
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from app.config import settings
from app.db.connection import Database
from app.spec import TaskStatus
from app.state.machine import default_transition_table

STREAM_TASK_EVENTS = "task_events"
KIND_TASK_ESCALATED = "task.escalated"
_STATUS_PENDING = "pending"
_STATUS_DELIVERED = "delivered"
_STATUS_FAILED = "failed"
_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1", "[::1]"})


class AlertConfigError(RuntimeError):
    """The notifier is configured in a way it refuses to run with."""


def _now() -> str:
    return datetime.now(UTC).isoformat()


def escalation_events() -> frozenset[str]:
    """Every event value whose frozen edge lands on ``escalated``.

    Derived from the transition table rather than hand-listed, so new
    escalation edges are alarmed on the day they land.
    """
    return frozenset(
        transition.event.value
        for transition in default_transition_table().transitions
        if transition.next is TaskStatus.ESCALATED
    )


@dataclass(frozen=True, slots=True)
class AlertRunReport:
    configured: bool
    detected: int
    delivered: int
    failed: int
    pending: int

    def as_json(self) -> dict[str, object]:
        return {
            "configured": self.configured,
            "detected": self.detected,
            "delivered": self.delivered,
            "failed": self.failed,
            "pending": self.pending,
        }


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Refuse redirects: the signature must not land on another host."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        return None


class AlertNotifier:
    def __init__(
        self,
        database: Database,
        *,
        webhook_url: str | None = None,
        secret: str | None = None,
        timeout_seconds: int | None = None,
        max_attempts: int | None = None,
        max_per_run: int = 50,
    ) -> None:
        self.database = database
        self.webhook_url = webhook_url if webhook_url is not None else settings.alert_webhook_url
        self.secret = secret if secret is not None else settings.alert_webhook_secret
        self.timeout_seconds = timeout_seconds or settings.alert_timeout_seconds
        self.max_attempts = max_attempts or settings.alert_max_attempts
        self.max_per_run = max_per_run

    # ---------------------------------------------------------------- runs

    def run_once(self) -> AlertRunReport:
        if self.webhook_url is None:
            return AlertRunReport(
                configured=False,
                detected=0,
                delivered=0,
                failed=0,
                pending=self._pending_count(),
            )
        self._validate_target()
        detected = self._scan()
        delivered, failed = self._deliver()
        return AlertRunReport(
            configured=True,
            detected=detected,
            delivered=delivered,
            failed=failed,
            pending=self._pending_count(),
        )

    # ------------------------------------------------------------ detection

    def _scan(self) -> int:
        wanted = escalation_events()
        detected = 0
        with self.database.transaction() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO alert_cursors(stream, cursor_seq, updated_at)"
                " VALUES (?, 0, ?)",
                (STREAM_TASK_EVENTS, _now()),
            )
            cursor = int(
                conn.execute(
                    "SELECT cursor_seq FROM alert_cursors WHERE stream = ?",
                    (STREAM_TASK_EVENTS,),
                ).fetchone()["cursor_seq"]
            )
            max_seq = int(
                conn.execute("SELECT COALESCE(MAX(seq), 0) AS m FROM task_events").fetchone()["m"]
            )
            if max_seq > cursor:
                rows = conn.execute(
                    "SELECT seq, event_name, aggregate_id, payload_json FROM task_events"
                    " WHERE seq > ? AND seq <= ? ORDER BY seq",
                    (cursor, max_seq),
                ).fetchall()
                for row in rows:
                    if str(row["event_name"]) not in wanted:
                        continue
                    detected += 1
                    conn.execute(
                        "INSERT OR IGNORE INTO alert_deliveries(delivery_id, kind, task_id,"
                        " source_seq, payload_json, status, attempts, last_error, created_at,"
                        " delivered_at) VALUES (?, ?, ?, ?, ?, 'pending', 0, NULL, ?, NULL)",
                        (
                            f"alert-{uuid.uuid4().hex}",
                            KIND_TASK_ESCALATED,
                            str(row["aggregate_id"]),
                            int(row["seq"]),
                            json.dumps(
                                {
                                    "event_name": str(row["event_name"]),
                                    "payload": json.loads(str(row["payload_json"])),
                                },
                                ensure_ascii=False,
                                sort_keys=True,
                            ),
                            _now(),
                        ),
                    )
                conn.execute(
                    "UPDATE alert_cursors SET cursor_seq = ?, updated_at = ?"
                    " WHERE stream = ? AND cursor_seq = ?",
                    (max_seq, _now(), STREAM_TASK_EVENTS, cursor),
                )
                # rowcount 0 = another notifier advanced first; our inserts were
                # idempotent (UNIQUE(kind, source_seq)), so this is a safe no-op.
        return detected

    # ------------------------------------------------------------ delivery

    def _deliver(self) -> tuple[int, int]:
        rows = self.database.read(
            "SELECT * FROM alert_deliveries WHERE status = ? ORDER BY created_at LIMIT ?",
            (_STATUS_PENDING, self.max_per_run),
        )
        delivered = 0
        failed = 0
        for row in rows:
            body = self._body(row)
            status_code = None
            error: str | None = None
            try:
                status_code = self._post(body, str(row["delivery_id"]))
            except urllib.error.HTTPError as exc:
                error = f"HTTP {exc.code}"
            except Exception as exc:  # noqa: BLE001 - network failures are the product
                error = f"{type(exc).__name__}: {exc}"
            if status_code is not None and 200 <= status_code < 300:
                with self.database.transaction() as conn:
                    conn.execute(
                        "UPDATE alert_deliveries SET status = ?, attempts = attempts + 1,"
                        " last_error = NULL, delivered_at = ? WHERE delivery_id = ?",
                        (_STATUS_DELIVERED, _now(), str(row["delivery_id"])),
                    )
                delivered += 1
                continue
            attempts = int(row["attempts"]) + 1
            parked = attempts >= self.max_attempts
            with self.database.transaction() as conn:
                conn.execute(
                    "UPDATE alert_deliveries SET status = ?, attempts = ?, last_error = ?"
                    " WHERE delivery_id = ?",
                    (
                        _STATUS_FAILED if parked else _STATUS_PENDING,
                        attempts,
                        error or f"HTTP {status_code}",
                        str(row["delivery_id"]),
                    ),
                )
            failed += 1
        return delivered, failed

    def _body(self, row: sqlite3.Row) -> bytes:
        stored = json.loads(str(row["payload_json"]))
        return json.dumps(
            {
                "alert": str(row["kind"]),
                "task_id": row["task_id"],
                "source_seq": int(row["source_seq"]),
                "created_at": str(row["created_at"]),
                "payload": stored,
            },
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")

    def _post(self, body: bytes, delivery_id: str) -> int:
        signature = hmac.new(self.secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
        request = urllib.request.Request(
            str(self.webhook_url),
            data=body,
            method="POST",
            headers={
                "Content-Type": "application/json",
                "X-AP-Signature": f"sha256={signature}",
                "X-AP-Delivery": delivery_id,
            },
        )
        opener = urllib.request.build_opener(_NoRedirect)
        with opener.open(request, timeout=self.timeout_seconds) as response:
            return int(response.status)

    # ------------------------------------------------------------- helpers

    def _validate_target(self) -> None:
        if self.webhook_url is None:
            raise AlertConfigError("webhook url is not configured")
        if not self.secret:
            raise AlertConfigError("AP_ALERT_WEBHOOK_SECRET is required when the webhook is on")
        parsed = urllib.parse.urlparse(self.webhook_url)
        host = (parsed.hostname or "").lower()
        if parsed.scheme == "https":
            return
        if parsed.scheme == "http" and host in _LOOPBACK_HOSTS:
            return
        raise AlertConfigError("alert webhook must be https (http is allowed for loopback only)")

    def _pending_count(self) -> int:
        row = self.database.read(
            "SELECT COUNT(*) AS n FROM alert_deliveries WHERE status = ?", (_STATUS_PENDING,)
        )[0]
        return int(row["n"])
