"""Where a harness CLI's own output lives *while it runs*.

The member worker drives the real CLI as a child process. Until now the child's
stdout only existed in a temp file and was read when the child exited -- so a
run that took six minutes left six minutes of silence in the platform's record
(the failure evidence kept the tail, but a successful run kept nothing).

The worker now tees the child's stdout/stderr into the data dir as it streams,
and this module owns the file names so the writer (the member worker) and the
reader (the platform's read route) agree on them. The office shows that tail as
the harness's own words; without it the screen can only describe what a
harness *did*.

These files are plain text on the platform's own disk: the worker and the
server share a data directory. A deployment with the worker on another host
would need a transport instead of a file.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

#: Where the run records and the streamed output live.
LOG_DIRNAME = "formal-runs"

#: How much of the tail the read route hands out. The office screen shows the
#: last screenful; the route keeps a little more so a client can page.
MAX_TAIL_BYTES = 32768
MAX_TAIL_LINES = 200


def run_dir(data_dir: Path | str) -> Path:
    return Path(data_dir) / LOG_DIRNAME


def log_paths(data_dir: Path | str, task_id: str, attempt_id: str) -> tuple[Path, Path]:
    """The (stdout, stderr) log paths for one attempt.

    Named after the attempt, not the task: a task may be attempted several
    times and each attempt has its own transcript.
    """
    base = run_dir(data_dir) / f"{task_id}.{attempt_id}"
    return (
        base.with_name(base.name + ".log"),
        base.with_name(base.name + ".err.log"),
    )


#: A run that has no task and no attempt (a review of a plan, the
#: administrator's own refinement) still ran a real CLI, and its words are
#: worth keeping. These names are what makes such a run findable later:
#: ``<kind>__<key>__<harness>.log``.
CONVERSATION_SEP = "__"


def conversation_paths(
    data_dir: Path | str, *, kind: str, key: str, harness_id: str
) -> tuple[Path, Path]:
    """The (stdout, stderr) log paths for a CLI run that owns no task.

    A member reviewing a plan runs its CLI exactly like it runs a task, but
    there is no attempt to hang the transcript on -- so the run used to leave
    **no trace at all**: the window's conversation showed only the platform's
    own records, and the CLI's session was invisible.

    The name carries the three things needed to find it again: what kind of
    run it was, which conversation it belongs to, and which window ran it.
    """
    parts = [kind, key, harness_id]
    safe = CONVERSATION_SEP.join(_safe_name(part) for part in parts)
    base = run_dir(data_dir) / safe
    return (
        base.with_name(base.name + ".log"),
        base.with_name(base.name + ".err.log"),
    )


def _safe_name(value: str) -> str:
    """A name that is safe on disk and still readable in a file listing."""
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", str(value or "")).strip("-")
    return cleaned or "unknown"


def parse_conversation_name(name: str) -> dict[str, str] | None:
    """Read back what ``conversation_paths`` wrote, or None if it is not ours.

    The task-attempt logs live in the same directory under a different shape
    (``<task>.<attempt>.log``), so the separator is what tells them apart.
    """
    stem = name
    for suffix in (".err.log", ".log"):
        if stem.endswith(suffix):
            stem = stem[: -len(suffix)]
            break
    else:
        return None
    parts = stem.split(CONVERSATION_SEP)
    if len(parts) != 3 or not all(parts):
        return None
    return {"kind": parts[0], "key": parts[1], "harness_id": parts[2]}


#: The mask a line with a credential-looking value gets.
REDACTED_LINE = "[这一行含疑似凭据，平台已遮挡]"

#: Credentials in a log usually carry a prefix (``OPENAI_API_KEY=...``), and the
#: outbound scan's word-boundary rule cannot see that shape: the ``_`` before
#: ``API`` is a word character, so ``\bapi_key\b`` never matches inside
#: ``OPENAI_API_KEY``.  Blocking a write and masking a log line are opposite
#: trades -- the scan prefers a false negative (a report item) over a false
#: positive (real work blocked), while masking one extra log line costs almost
#: nothing next to leaking a credential.  So the log side is deliberately wider.
_LOG_CREDENTIAL = re.compile(
    r"(?i)\b[A-Za-z0-9_]*(?:api[_-]?key|secret|passwd|password|token)\b\s*[=:]\s*"
    r"[\"']?([A-Za-z0-9_+/=.-]{20,})"
)


def redact(text: str) -> tuple[str, int]:
    """Mask the lines that carry a credential-looking value.

    The platform masks them **before** anybody reads the log: the assistant (and
    every other reader) never sees the raw value.  The count of masked lines
    goes with the text, so whoever quotes it can say how much was hidden
    instead of passing it off as complete.
    """
    from app.security.scan import scan_text

    kept: list[str] = []
    masked = 0
    for line in text.splitlines():
        if scan_text(line, location="cli-output") or _LOG_CREDENTIAL.search(line):
            masked += 1
            kept.append(REDACTED_LINE)
            continue
        kept.append(line)
    return "\n".join(kept), masked


def read_tail(
    path: Path | str, *, max_bytes: int = MAX_TAIL_BYTES, max_lines: int = MAX_TAIL_LINES
) -> dict[str, Any]:
    """The tail of one log file, plus the facts about it.

    A missing file is not an error: the CLI may not have started yet.
    """
    target = Path(path)
    if not target.exists():
        return {"text": "", "bytes": 0, "updated_at": None, "lines": 0}
    try:
        stat = target.stat()
        with target.open("rb") as handle:
            handle.seek(max(0, stat.st_size - max_bytes))
            data = handle.read()
    except OSError:
        return {"text": "", "bytes": 0, "updated_at": None, "lines": 0}
    text = data.decode("utf-8", errors="replace")
    lines = text.splitlines()
    tail = lines[-max_lines:]
    return {
        "text": "\n".join(tail),
        "bytes": stat.st_size,
        "lines": len(lines),
        "updated_at": datetime.fromtimestamp(stat.st_mtime, UTC).isoformat(),
    }
