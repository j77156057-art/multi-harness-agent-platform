"""Mark the usage records whose tokens were never counted.

The pool's streamed paths used to account **zero** tokens (the upstream puts
the usage in its last event and the pool was not looking), and the CLI slots
never reported their usage at all.  Those zeros are **not a measurement** --
they are the absence of one.  Left as they are, the office says "this call
used no tokens", which reads as "this call was free".

This writes the corrections that say *not recorded* instead, next to the log
(the log itself is append-only: it is the record of what happened, and
rewriting it would change the past).  It never invents a number -- the
tokens stay unknown, with the reason.

Usage::

    python -m scripts.backfill_usage_corrections [--usage <path>] [--dry-run]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

#: The notes that mean "the usage was never counted".
UNC_COUNTED_NOTES = ("streamed", "streamed (responses)", "served by the CLI slot")

REASON = (
    "池子当时还不看流式的 usage（或 CLI 槽不报 usage）：这一次的 token 从来没被数过，"
    "不是数出来是 0。provider 侧只有账号层面的汇总，无法归到单次请求，所以这里只能"
    "记成「未记上」"
)


def corrections_for(usage_path: Path) -> list[dict[str, object]]:
    """The corrections the log needs: the records whose usage is unknown."""
    found: list[dict[str, object]] = []
    if not usage_path.exists():
        return found
    for line in usage_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except ValueError:
            continue
        if int(record.get("prompt_tokens") or 0) or int(record.get("completion_tokens") or 0):
            continue  # it was counted
        if str(record.get("note") or "") not in UNC_COUNTED_NOTES:
            continue  # a call that really used nothing
        found.append(
            {
                "at": record.get("at"),
                "upstream": record.get("upstream"),
                "model": record.get("model"),
                "consumer": record.get("consumer"),
                "tokens_unknown": True,
                "reason": REASON,
            }
        )
    return found


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="scripts.backfill_usage_corrections")
    parser.add_argument(
        "--usage",
        default=str(Path("data") / "pool" / "usage.jsonl"),
        help="the pool's usage log (default: data/pool/usage.jsonl)",
    )
    parser.add_argument("--dry-run", action="store_true", help="print, do not write")
    args = parser.parse_args(argv)

    usage_path = Path(args.usage)
    corrections = corrections_for(usage_path)
    target = usage_path.parent / "usage-corrections.jsonl"
    print(f"{usage_path}: {len(corrections)} 条记录的 usage 没被数过")
    for correction in corrections:
        print(
            f"  {correction['at']} {correction['upstream']} {correction['model']}"
            f" {correction['consumer']}"
        )
    if args.dry_run:
        print("（--dry-run：没有写）")
        return 0
    if not corrections:
        print("没有要补的。")
        return 0
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("a", encoding="utf-8") as handle:
        for correction in corrections:
            handle.write(json.dumps(correction, ensure_ascii=False) + "\n")
    print(f"已写入 {target}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
