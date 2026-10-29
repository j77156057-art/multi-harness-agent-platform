"""`python -m app.agenttools.cli` -- the platform's own harness, as a process.

It is launched exactly like every other Harness: the worker claims a task,
provisions a worktree, builds a work order and runs this command inside that
worktree.  The difference is who executes the tool calls.  With a CLI harness
the answer is "the CLI, using its own tools"; here it is "the platform, using
the tools in :mod:`app.agenttools.registry`, on behalf of a model that can only
write text".

Arguments, all of them facts the worker already has:

* ``--bundle`` -- a per-attempt JSON file with ``base_url`` / ``token`` /
  ``model`` (written by the adapter, removed after the run, so the token is
  never in argv);
* ``--order`` -- the work order text, in a file for the same reason (a Windows
  command line is ~32k and a work order is not);
* ``--scope`` -- the task's ``exclusive_paths``; also passed through
  ``AGENT_OFFICE_SCOPE`` so the adapter does not have to guess.

Exit status is the same contract the other CLIs keep: ``0`` means "a
deliverable is in the worktree", anything else means "no deliverable", and the
last line of stderr is the sentence a human reads on the task card.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path


def _utf8_stdout() -> None:
    """Chinese output must not die on a cp936 console."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):  # pragma: no cover - already utf-8
            pass


RESULT_MARKER = "PLATFORM-TOOLS-RESULT"


def main(argv: list[str] | None = None) -> int:
    _utf8_stdout()
    parser = argparse.ArgumentParser(prog="agenttools", add_help=True)
    parser.add_argument("--bundle", required=True, help="per-attempt model channel file")
    parser.add_argument("--order", required=True, help="work order file")
    parser.add_argument("--scope", default="", help="comma-separated authorized paths")
    parser.add_argument("--read-only", action="store_true", help="review mode: look, never change")
    parser.add_argument("--max-steps", type=int, default=0)
    parser.add_argument("--max-seconds", type=float, default=0.0)
    args = parser.parse_args(argv)

    from app.agenttools.floor import Floor
    from app.agenttools.loop import PlatformLoop
    from app.agenttools.model import ChannelFailed, from_bundle
    from app.agenttools.registry import build_registry

    try:
        bundle = json.loads(Path(args.bundle).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        print(f"模型通道配置读不到：{exc}", file=sys.stderr)
        return 2
    work_order = ""
    try:
        work_order = Path(args.order).read_text(encoding="utf-8")
    except OSError as exc:
        print(f"工单读不到：{exc}", file=sys.stderr)
        return 2
    scope = _scope_of(args.scope)
    floor = Floor(root=Path.cwd(), paths=tuple(scope))

    try:
        channel = from_bundle(bundle)
    except ChannelFailed as exc:
        print(str(exc), file=sys.stderr)
        return 3

    tools = build_registry(floor, read_only=args.read_only)
    loop = PlatformLoop(
        channel,
        tools,
        floor=floor,
        goal=work_order,
        max_steps=args.max_steps or _env_int("AP_TOOL_MAX_STEPS", 24),
        max_seconds=args.max_seconds or _env_float("AP_TOOL_MAX_SECONDS", 900.0),
    )
    print(f"[platform] 模型通道：{channel.base_url} · {channel.model}", flush=True)
    print(f"[platform] 工作树：{floor.root.as_posix()}", flush=True)
    if scope:
        print("[platform] 授权落点：" + "、".join(scope), flush=True)

    result = loop.run()
    for step in result.steps:
        first = (step.observation or "").strip().splitlines()
        head = first[0][:200] if first else "（空）"
        print(f"[{step.no}] {step.tool or '（没写工具）'} → {head}", flush=True)

    summary = {
        "ok": result.ok,
        "steps": len(result.steps),
        "summary": result.summary,
        "error": result.error,
        "tool_calls": sum(1 for step in result.steps if step.tool),
    }
    print(RESULT_MARKER + " " + json.dumps(summary, ensure_ascii=False), flush=True)
    if result.ok:
        return 0
    print(f"没有交出可用的改动：{result.error or '模型没有完成这条活'}", file=sys.stderr)
    return 1


def _scope_of(raw: str) -> list[str]:
    text = str(raw or "").strip()
    if not text:
        text = os.environ.get("AGENT_OFFICE_SCOPE", "").strip()
    if not text:
        return []
    if text.startswith("["):
        try:
            parsed = json.loads(text)
        except ValueError:
            return []
        return [str(item) for item in parsed if str(item).strip()] if isinstance(parsed, list) else []
    return [piece.strip() for piece in text.split(",") if piece.strip()]


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, "") or default)
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, "") or default)
    except ValueError:
        return default


if __name__ == "__main__":  # pragma: no cover - process entry
    raise SystemExit(main())
