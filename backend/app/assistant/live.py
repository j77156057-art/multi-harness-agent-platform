"""What the platform assistant is doing *right now*, per group.

The trace files record what a finished run thought and saw.  They do not
record the run that is **happening**: a question asked three seconds ago has
no trace yet, and the office had no way to say "it got your message" --
so the person waited in front of a panel that looked broken.

This module is that missing half.  It is a small JSON file per group,
rewritten in place as the run progresses:

* "received"  -- the question arrived (so the office can say "it got it");
* "step"      -- a tool call happened (so the panel can show progress);
* "thinking"  -- the assistant is composing the answer;
* "done"      -- the answer was delivered.

The file is a **cache of what already happened**, not a source of truth:
the delivered answer is a message in the group ledger, and the trace file
remains the record of the reasoning.  Nothing here is load-bearing -- if
this file is missing or stale, the office simply shows the older state.
"""

from __future__ import annotations

import contextlib
import json
import os
import tempfile
import time
from pathlib import Path
from typing import Any

#: How long a "still running" marker stays believable without a refresh.
#: The assistant polls every few seconds; past this the office stops
#: claiming "it is working on it" and says the truth instead.
STALE_AFTER_SECONDS = 300.0

#: The states a live record can be in.
STATE_RECEIVED = "received"
STATE_RUNNING = "running"
STATE_ANSWERING = "answering"
STATE_DONE = "done"
STATE_FAILED = "failed"


def live_dir(data_dir: Path | str) -> Path:
    """Where the per-group live files live (beside the trace files).

    The path is **absolute**: a worker started from another directory would
    otherwise drop its live files next to itself instead of next to the
    platform's data dir (2026-11-18 真机走查实测：文件落到了 backend/ 下）。
    """
    return Path(data_dir).resolve() / "assistant" / "live"


def _path(data_dir: Path | str, group_id: str) -> Path:
    safe = "".join(ch if ch.isalnum() or ch in "-_." else "_" for ch in str(group_id))
    return live_dir(data_dir) / f"{safe}.json"


def write_state(data_dir: Path | str, group_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Publish the current state of a group's assistant, atomically.

    The write is atomic (write a temp file, then replace) so a reader never
    sees half a JSON document -- the office polls this file, and a torn read
    would show as "the assistant said nothing".
    """
    directory = live_dir(data_dir)
    directory.mkdir(parents=True, exist_ok=True)
    record = dict(payload)
    record["group_id"] = group_id
    record["updated_at"] = time.time()
    path = _path(data_dir, group_id)
    handle, temp_name = tempfile.mkstemp(dir=directory, suffix=".tmp")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            json.dump(record, stream, ensure_ascii=False)
        os.replace(temp_name, path)
    except OSError:
        # A lost live marker is not a lost answer: the message ledger still
        # has the reply.  Leave the previous file alone.
        with contextlib.suppress(OSError):
            os.unlink(temp_name)
        raise
    return record


def read_state(data_dir: Path | str, group_id: str) -> dict[str, Any] | None:
    """The current state of a group's assistant, or None when there is none.

    A record older than :data:`STALE_AFTER_SECONDS` is reported as stale
    rather than as "running": the office must not keep spinning forever on
    a marker nobody refreshed.
    """
    path = _path(data_dir, group_id)
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return None
    try:
        record = json.loads(raw)
    except ValueError:
        return None
    if not isinstance(record, dict):
        return None
    updated = float(record.get("updated_at") or 0.0)
    record["stale"] = bool(updated and (time.time() - updated) > STALE_AFTER_SECONDS)
    return record


def clear_state(data_dir: Path | str, group_id: str) -> None:
    """Forget a group's live state (the run ended and said so)."""
    try:
        _path(data_dir, group_id).unlink()
    except OSError:
        return


__all__ = [
    "STATE_ANSWERING",
    "STATE_DONE",
    "STATE_FAILED",
    "STATE_RECEIVED",
    "STATE_RUNNING",
    "STALE_AFTER_SECONDS",
    "clear_state",
    "live_dir",
    "read_state",
    "write_state",
]
