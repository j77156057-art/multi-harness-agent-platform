"""SQLite → JSONL 异步导出（R10 §15 / ADR-055 / FIN-09；jsonl-export）。

The §15 contract, enforced here:

* SQLite is the single fact source; the JSONL file is a derived artefact. The
  exporter runs **after** commits -- never inside the writing transaction
  (no dual writes).
* ``jsonl_exports`` carries the watermark (``exported_seq``) plus file_hash /
  status / attempts / last_error / updated_at.
* Events are read from ``exported_seq + 1`` in order; the new file is written
  to a temp path, fsynced, atomically ``os.replace``d, and only then does the
  watermark advance (a conditional UPDATE keyed on the old value).
* A failed run lands in the retry queue (``status='failed'``, ``attempts+1``,
  ``last_error``); the next run compares ``max(seq)`` with the watermark and
  backfills.
* Recovery de-duplicates by **seq**: global seq is shared with other streams
  (messages), so gaps in this stream are normal -- what must never happen is a
  duplicate or a missing event. A crash between the replace and the watermark
  advance leaves an uncommitted tail, which the next run trims by the
  watermark before appending.

Alarm surface (recorded -- this repository has no pager/webhook): the
``jsonl_exports`` row plus the CLI's stderr and exit codes.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from app.config import settings
from app.db.connection import Database
from app.events.store import EventRecord, EventStore

STREAM_TASK_EVENTS = "task_events"


class ExportInvariantError(RuntimeError):
    """The file this exporter built disagrees with the database."""


def _now() -> str:
    return datetime.now(UTC).isoformat()


@dataclass(frozen=True, slots=True)
class ExportReport:
    stream: str
    records_read: int
    exported_seq: int
    lag: int
    failed: bool
    detail: str | None = None

    def as_json(self) -> dict[str, object]:
        return {
            "stream": self.stream,
            "records_read": self.records_read,
            "exported_seq": self.exported_seq,
            "lag": self.lag,
            "failed": self.failed,
            "detail": self.detail,
        }


@dataclass(frozen=True, slots=True)
class ReconcileReport:
    stream: str
    ok: bool
    db_events: int
    db_last_seq: int
    exported_seq: int
    file_lines: int
    file_last_seq: int
    hash_matches: bool
    problems: tuple[str, ...]

    def as_json(self) -> dict[str, object]:
        return {
            "stream": self.stream,
            "ok": self.ok,
            "db_events": self.db_events,
            "db_last_seq": self.db_last_seq,
            "exported_seq": self.exported_seq,
            "file_lines": self.file_lines,
            "file_last_seq": self.file_last_seq,
            "hash_matches": self.hash_matches,
            "problems": list(self.problems),
        }


class JsonlExporter:
    """One stream (``task_events``); the table is keyed by stream for later ones."""

    def __init__(self, database: Database, *, export_dir: Path | None = None) -> None:
        self.database = database
        self.export_dir = Path(export_dir) if export_dir is not None else settings.export_dir
        self.store = EventStore(database)

    @property
    def stream(self) -> str:
        return STREAM_TASK_EVENTS

    @property
    def path(self) -> Path:
        return self.export_dir / f"{self.stream}.jsonl"

    # ------------------------------------------------------------------ runs

    def export_once(self) -> ExportReport:
        """Run one export; failures are recorded and reported, never raised."""
        try:
            return self._export()
        except Exception as exc:  # noqa: BLE001 - the failure IS the product here
            self._record_failure(exc)
            watermark = self._watermark()
            return ExportReport(
                stream=self.stream,
                records_read=0,
                exported_seq=watermark,
                lag=self._lag(watermark),
                failed=True,
                detail=f"{type(exc).__name__}: {exc}"[:500],
            )

    def _export(self) -> ExportReport:
        self.export_dir.mkdir(parents=True, exist_ok=True)
        watermark = self._ensure_state()
        kept, problems = self._read_prefix(watermark)
        events = list(self.store.iter_since(0 if problems else watermark))
        rebuild_note = "; ".join(problems) if problems else None
        content = [self._line(record) for record in events]
        if not problems:
            content = kept + content
        target = events[-1].seq if events else watermark

        if len(content) != self._count_through(target):
            # The kept prefix disagrees with the database (truncation, edit, or
            # a watermark we cannot trust): rebuild once from the fact source.
            events = list(self.store.iter_since(0))
            target = events[-1].seq if events else watermark
            content = [self._line(record) for record in events]
            rebuild_note = "kept prefix disagreed with the database; rebuilt from seq 0"
        if len(content) != self._count_through(target):
            raise ExportInvariantError(
                f"{len(content)} lines for {self._count_through(target)} events through seq {target}"
            )

        desired = "".join(line + "\n" for line in content)
        current = self.path.read_text(encoding="utf-8") if self.path.exists() else None
        if content:
            if desired != current:
                self._atomic_write(desired)
            file_hash = hashlib.sha256(desired.encode("utf-8")).hexdigest()
        else:
            # Nothing to export yet: leave any (nonexistent) file alone and
            # record "no content" -- reconcile() treats the empty state as ok.
            file_hash = None
        self._advance(previous=watermark, target=target, file_hash=file_hash)
        return ExportReport(
            stream=self.stream,
            records_read=len(events),
            exported_seq=target,
            lag=self._lag(target),
            failed=False,
            detail=rebuild_note,
        )

    # ------------------------------------------------------------ reconcile

    def reconcile(self) -> ReconcileReport:
        """Read-only 对账: file vs watermark vs fact source."""
        problems: list[str] = []
        state_rows = self.database.read(
            "SELECT exported_seq, file_hash FROM jsonl_exports WHERE stream = ?", (self.stream,)
        )
        if not state_rows:
            problems.append("no jsonl_exports row for the stream")
            exported_seq = 0
            stored_hash: str | None = None
        else:
            exported_seq = int(state_rows[0]["exported_seq"])
            stored_hash = state_rows[0]["file_hash"]

        row = self.database.read(
            "SELECT COUNT(*) AS n, COALESCE(MAX(seq), 0) AS m FROM task_events"
        )[0]
        db_events = int(row["n"])
        db_last_seq = int(row["m"])
        expected_file_lines = self._count_through(exported_seq)

        file_lines = 0
        file_last_seq = 0
        hash_matches = True  # nothing to compare when there is no expected content
        if not self.path.exists():
            if expected_file_lines > 0:
                problems.append("export file missing")
                hash_matches = False
        else:
            raw = self.path.read_bytes()
            previous = 0
            for index, line in enumerate(raw.decode("utf-8").splitlines(), start=1):
                if not line:
                    continue
                try:
                    seq = int(json.loads(line)["seq"])
                except (ValueError, KeyError, json.JSONDecodeError):
                    problems.append(f"unparseable line {index}")
                    break
                if seq <= previous:
                    problems.append(f"line {index} has seq {seq} after {previous} (not increasing)")
                    break
                if seq > exported_seq:
                    problems.append(f"line {index} has seq {seq} beyond watermark {exported_seq}")
                    break
                previous = seq
                file_lines += 1
                file_last_seq = seq
            hash_matches = stored_hash is not None and hashlib.sha256(raw).hexdigest() == str(
                stored_hash
            )
            if not raw.endswith(b"\n") and raw:
                problems.append("file does not end with a newline (partial tail)")

        if file_lines != expected_file_lines:
            problems.append(
                f"file has {file_lines} lines, database has {expected_file_lines} events"
                f" through watermark {exported_seq}"
            )
        if db_last_seq != exported_seq:
            problems.append(f"lag: db max seq {db_last_seq}, watermark {exported_seq}")
        if not hash_matches:
            problems.append("file_hash mismatch")
        return ReconcileReport(
            stream=self.stream,
            ok=not problems,
            db_events=db_events,
            db_last_seq=db_last_seq,
            exported_seq=exported_seq,
            file_lines=file_lines,
            file_last_seq=file_last_seq,
            hash_matches=hash_matches,
            problems=tuple(problems),
        )

    # ---------------------------------------------------------------- state

    def _watermark(self) -> int:
        rows = self.database.read(
            "SELECT exported_seq FROM jsonl_exports WHERE stream = ?", (self.stream,)
        )
        return int(rows[0]["exported_seq"]) if rows else 0

    def _ensure_state(self) -> int:
        with self.database.transaction() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO jsonl_exports(stream, exported_seq, file_hash, status,"
                " attempts, last_error, updated_at) VALUES (?, 0, NULL, 'idle', 0, NULL, ?)",
                (self.stream, _now()),
            )
            row = conn.execute(
                "SELECT exported_seq FROM jsonl_exports WHERE stream = ?", (self.stream,)
            ).fetchone()
        return int(row["exported_seq"])

    def _advance(self, *, previous: int, target: int, file_hash: str | None) -> None:
        with self.database.transaction() as conn:
            cursor = conn.execute(
                "UPDATE jsonl_exports SET exported_seq = ?, file_hash = ?, status = 'ok',"
                " attempts = 0, last_error = NULL, updated_at = ?"
                " WHERE stream = ? AND exported_seq = ?",
                (target, file_hash, _now(), self.stream, previous),
            )
            if cursor.rowcount == 1:
                return
            row = conn.execute(
                "SELECT exported_seq FROM jsonl_exports WHERE stream = ?", (self.stream,)
            ).fetchone()
            if row is not None and int(row["exported_seq"]) >= target:
                # A concurrent exporter already advanced past this run's target;
                # both wrote the same derived content, so this is a no-op.
                return
            raise ExportInvariantError(
                f"watermark advanced backwards for {self.stream}: {previous} -> {target}"
            )

    def _record_failure(self, exc: Exception) -> None:
        """Best-effort: the report is the product, recording is secondary.

        A database that is behind on migrations cannot carry the bookkeeping
        row; that must not turn a reported failure into a traceback.
        """
        message = f"{type(exc).__name__}: {exc}"[:500]
        try:
            with self.database.transaction() as conn:
                conn.execute(
                    "INSERT INTO jsonl_exports(stream, exported_seq, file_hash, status, attempts,"
                    " last_error, updated_at) VALUES (?, 0, NULL, 'failed', 1, ?, ?)"
                    " ON CONFLICT(stream) DO UPDATE SET status = 'failed',"
                    " attempts = jsonl_exports.attempts + 1, last_error = excluded.last_error,"
                    " updated_at = excluded.updated_at",
                    (self.stream, message, _now()),
                )
        except Exception:  # noqa: BLE001 - recording must never raise
            pass

    def _lag(self, watermark: int) -> int:
        row = self.database.read("SELECT COALESCE(MAX(seq), 0) AS m FROM task_events")[0]
        return max(0, int(row["m"]) - watermark)

    def _count_through(self, seq: int) -> int:
        row = self.database.read("SELECT COUNT(*) AS n FROM task_events WHERE seq <= ?", (seq,))[0]
        return int(row["n"])

    # ---------------------------------------------------------------- file io

    def _read_prefix(self, watermark: int) -> tuple[list[str], list[str]]:
        """Lines up to the watermark, plus problems found on the way.

        Lines beyond the watermark are an uncommitted tail (crash between the
        atomic replace and the watermark advance) and are trimmed silently --
        that is the R10 §15 recovery-by-seq rule. A hole *within* the
        watermark is not detectable here; the caller's line-count check
        against the database catches it and rebuilds.
        """
        if not self.path.exists():
            return [], []
        problems: list[str] = []
        kept: list[str] = []
        previous = 0
        for index, line in enumerate(self.path.read_text(encoding="utf-8").splitlines(), start=1):
            if not line:
                continue
            try:
                seq = int(json.loads(line)["seq"])
            except (ValueError, KeyError, json.JSONDecodeError):
                problems.append(f"unparseable line {index} within the watermark")
                break
            if seq > watermark:
                break
            if seq <= previous:
                problems.append(f"line {index} has seq {seq} after {previous}")
                break
            kept.append(line)
            previous = seq
        return kept, problems

    def _atomic_write(self, payload: str) -> None:
        """Temp file + fsync + atomic replace -- never a torn export file."""
        data = payload.encode("utf-8")
        fd, tmp_name = tempfile.mkstemp(
            dir=self.export_dir, prefix=f".{self.stream}.", suffix=".tmp"
        )
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp_name, self.path)
        finally:
            if os.path.exists(tmp_name):
                os.unlink(tmp_name)

    @staticmethod
    def _line(record: EventRecord) -> str:
        return json.dumps(
            {
                "seq": record.seq,
                "event_name": record.event_name,
                "aggregate_type": record.aggregate_type,
                "aggregate_id": record.aggregate_id,
                "actor_type": record.actor_type,
                "actor_id": record.actor_id,
                "payload": record.payload,
                "payload_hash": record.payload_hash,
                "schema_version": record.schema_version,
                "created_at": record.created_at,
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )
