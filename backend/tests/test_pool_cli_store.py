"""The usage a CLI recorded in **its own** store.

The opencode CLI does not put its usage on stdout, but it keeps it with the
assistant message.  The message id is in the CLI's own output, so the lookup
is exact -- no time window, no guessing which messages belonged to a turn.
"""

from __future__ import annotations

import json
import sqlite3

from app.pool.cli_usage import opencode_message_usage, opencode_store_paths


def _store(tmp_path) -> str:
    """A store that looks like the opencode CLI's, with one message in it."""
    (tmp_path / "opencode").mkdir(exist_ok=True)
    path = tmp_path / "opencode" / "opencode.db"
    connection = sqlite3.connect(path)
    connection.execute(
        "CREATE TABLE session_message(id TEXT PRIMARY KEY, session_id TEXT,"
        " type TEXT, seq INTEGER, time_created INTEGER, time_updated INTEGER, data TEXT)"
    )
    connection.execute(
        "INSERT INTO session_message(id, session_id, type, seq, time_created, time_updated, data)"
        " VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            "msg_1",
            "ses_1",
            "assistant",
            1,
            1700000000000,
            1700000000000,
            json.dumps(
                {
                    "model": {"id": "minimax-m3.1-flash-free"},
                    "tokens": {"input": 5600, "output": 2, "reasoning": 0, "cache": {"read": 7}},
                }
            ),
        ),
    )
    connection.commit()
    connection.close()
    return str(path)


def test_the_usage_is_read_from_the_clis_own_store(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    _store(tmp_path)

    usage = opencode_message_usage(["msg_1"])

    assert usage == {"prompt_tokens": 5600, "completion_tokens": 2}


def test_a_message_that_is_not_there_means_no_usage_not_zero(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    _store(tmp_path)

    assert opencode_message_usage(["msg_nope"]) is None
    assert opencode_message_usage([]) is None


def test_a_store_that_is_not_there_means_no_usage(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "nothing"))

    assert opencode_message_usage(["msg_1"]) is None


def test_the_store_path_follows_the_clis_own_rule(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "profile"))
    monkeypatch.setenv("HOME", str(tmp_path / "profile"))

    paths = [str(path) for path in opencode_store_paths()]

    assert str(tmp_path / "opencode" / "opencode.db") in paths
    assert str(tmp_path / "profile" / ".local" / "share" / "opencode" / "opencode.db") in paths
