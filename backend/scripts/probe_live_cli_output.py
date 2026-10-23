"""用**真的** harness CLI 证明 tee 边跑边落盘。

这不是平台任务，只是一个隔离的试验：真的 `claude` CLI 在临时目录里跑，我们
用平台自己的 `SubprocessRunner` 拉起它，把 stdout 抄进日志文件，然后证明
运行期间日志文件就已经有内容了——这正是"办公室屏幕能显示 harness 原话"所
依赖的那一环。

用法：python -m scripts.probe_live_cli_output <minutes>
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

from app.adapter.harnesses.cli import SubprocessRunner


def main() -> int:
    minutes = float(sys.argv[1]) if len(sys.argv) > 1 else 1.0
    root = Path("D:/Temp/opencode/live-cli-probe")
    root.mkdir(parents=True, exist_ok=True)
    log = root / "claude.log"
    err = root / "claude.err.log"
    for path in (log, err):
        path.unlink(missing_ok=True)

    argv = [
        "claude",
        "-p",
        "--output-format",
        "json",
        "--permission-mode",
        "acceptEdits",
        "用 30 秒写一个 hello.py，只打印 hello world，然后停。",
    ]
    runner = SubprocessRunner()
    started = time.monotonic()
    import threading

    samples: list[dict[str, object]] = []

    def sample() -> None:
        while time.monotonic() - started < minutes * 60:
            time.sleep(5)
            samples.append(
                {
                    "at": round(time.monotonic() - started, 1),
                    "bytes": log.stat().st_size if log.exists() else 0,
                    "lines": len(log.read_text(encoding="utf-8", errors="replace").splitlines())
                    if log.exists()
                    else 0,
                }
            )
            if not runner_process_alive():
                return

    def runner_process_alive() -> bool:
        return not getattr(runner, "_probe_done", False)

    thread = threading.Thread(target=sample, daemon=True)
    thread.start()
    result = runner.run(
        argv,
        cwd=root,
        timeout_seconds=minutes * 60,
        log_path=log,
        err_log_path=err,
    )
    print(
        json.dumps(
            {
                "returncode": result.returncode,
                "duration_seconds": round(result.duration_seconds, 1),
                "log_bytes": log.stat().st_size if log.exists() else 0,
                "samples_during_the_run": samples,
                "log_head": log.read_text(encoding="utf-8", errors="replace")[:400]
                if log.exists()
                else "",
            },
            ensure_ascii=False,
            indent=1,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
