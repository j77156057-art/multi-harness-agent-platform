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
