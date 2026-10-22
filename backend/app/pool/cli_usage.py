"""The usage a CLI recorded in **its own** store.

Some CLIs do not put the usage on stdout -- the opencode CLI's JSON stream
carries the answer and the session, but not the tokens.  They do keep the
tokens in their own store, though: the assistant message carries them.

The message id is in the CLI's own output, so the lookup is **exact**: no time
window, no "which of these belonged to this turn", no guessing.  The store is
the CLI's and is read read-only; a store that is missing, locked, or does not
have the message simply means "no usage from this one" -- never a zero.
"""

from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path
from typing import Any

#: How long to wait for the CLI's last write to land.  The CLI writes the
#: message as it goes, but the final one may land just before it exits.
SETTLE_SECONDS = 0.4


def opencode_store_paths() -> list[Path]:
    """Where the opencode CLI keeps its own store, in the CLI's own order.

    The CLI honours ``XDG_DATA_HOME``; the member worker gives it one, so
    the pool follows the same rule and looks in the same place.
    """
    home = os.environ.get("XDG_DATA_HOME")
    roots = []
    if home:
        roots.append(Path(home) / "opencode")
    profile = os.environ.get("USERPROFILE") or os.environ.get("HOME") or ""
    if profile:
        roots.append(Path(profile) / ".local" / "share" / "opencode")
    return [root / "opencode.db" for root in roots]


def opencode_message_usage(message_ids: list[str]) -> dict[str, int] | None:
    """The tokens of those assistant messages, in the CLI's own store.

    ``None`` means "the CLI did not tell us" -- the caller must not turn that
    into a zero.
    """
    wanted = [message_id for message_id in message_ids if message_id]
    if not wanted:
        return None
    for path in opencode_store_paths():
        if not path.exists():
            continue
        try:
            rows = _read_messages(path, wanted)
        except (sqlite3.Error, OSError, ValueError):
            continue
        if not rows:
            continue
        prompt = 0
        completion = 0
        found = 0
        for data in rows:
            tokens = data.get("tokens")
            if not isinstance(tokens, dict):
                continue
            found += 1
            prompt += int(tokens.get("input") or tokens.get("input_tokens") or 0)
            completion += int(tokens.get("output") or tokens.get("output_tokens") or 0)
        if not found:
            continue
        return {"prompt_tokens": prompt, "completion_tokens": completion}
    return None


def _read_messages(path: Path, message_ids: list[str]) -> list[dict[str, Any]]:
    """The stored messages, read read-only."""
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=5.0)
    try:
        connection.row_factory = sqlite3.Row
        rows = []
        for message_id in message_ids:
            row = connection.execute(
                "SELECT data FROM session_message WHERE id = ?", (message_id,)
            ).fetchone()
            if not row or not row["data"]:
                continue
            try:
                data = json.loads(row["data"])
            except ValueError:
                continue
            if isinstance(data, dict):
                rows.append(data)
        return rows
    finally:
        connection.close()
