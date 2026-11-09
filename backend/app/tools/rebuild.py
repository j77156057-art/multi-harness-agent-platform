"""Rebuild the current projection from the event log.

Usage
-----
    # 1) 先给现有库补发投影 checkpoint（一次性或定期）
    python -m app.tools.rebuild --source data/state.db --snapshot

    # 2) 重建到新库
    python -m app.tools.rebuild --source data/state.db --to /tmp/rebuilt.db

    # 3) 补发 + 重建 + 逐表 diff（一条命令完成证明）
    python -m app.tools.rebuild --source data/state.db --snapshot \
        --to /tmp/rebuilt.db --verify

    # 4) 只看缺口
    python -m app.tools.rebuild --source data/state.db --report

Exit codes: 0 = 成功且 diff 全等；1 = 失败或有差异；2 = 参数错误。

为什么需要 ``--snapshot``
--------------------------
历史事件（本库 2,216 条）没有创建事件、没有 from/to 状态、attempt 收口字段也不在
payload 里，而 ``task_events`` 的触发器禁止 UPDATE。因此重建以「checkpoint 事件 +
其后生命周期事件」为准：``--snapshot`` 把每行投影写成一条 allowlist 内的
``schema_migration_applied`` 事件（payload 标注 ``projection.checkpoint.v1``）。
``--report`` 会把无法由事件重建的表逐个列出缺口原因。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from app.events.projection import (
    REBUILDABLE,
    append_checkpoints,
    diff,
    gap_report,
    rebuild,
)


def _print_report(source: Path, as_json: bool) -> int:
    report = gap_report(source)
    if as_json:
        print(json.dumps({"gaps": report}, ensure_ascii=False, indent=2))
        return 0
    print("不能由事件重建的表（具体缺口）：")
    for table, item in report.items():
        print(f"  {table:28s} rows={item['rows']:<5d} {item['gap']}")
    print("\n可由 checkpoint 重建的表：")
    for table in REBUILDABLE:
        print(f"  {table}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="app.tools.rebuild",
        description="从 task_events 重建 SQLite 投影，并与现库逐表 diff",
    )
    parser.add_argument("--source", required=True, help="要读取的事件库")
    parser.add_argument("--to", help="重建目标库；不存在则新建")
    parser.add_argument(
        "--snapshot",
        action="store_true",
        help="先向 source 补发投影 checkpoint 事件",
    )
    parser.add_argument("--verify", action="store_true", help="重建后与 source 逐表 diff")
    parser.add_argument("--report", action="store_true", help="输出缺口清单与行数")
    parser.add_argument("--force", action="store_true", help="允许覆盖已存在的目标库")
    parser.add_argument("--json", action="store_true", help="机器可读输出")
    args = parser.parse_args(argv)

    source = Path(args.source)
    if not source.exists():
        print(f"source 不存在：{source}", file=sys.stderr)
        return 2

    if args.report or (not args.to and not args.snapshot):
        return _print_report(source, args.json)

    if args.snapshot:
        from app.db.connection import Database

        written = append_checkpoints(Database(source))
        print(f"checkpoint 事件：写入 {written} 条（表数 {len(REBUILDABLE)}）")

    if not args.to:
        return 0

    try:
        result = rebuild(source, Path(args.to), force=args.force)
    except (FileExistsError, FileNotFoundError, RuntimeError) as exc:
        print(f"重建失败：{exc}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(result.as_dict(), ensure_ascii=False, indent=2))
    else:
        print(f"重建完成：{result.target}")
        print(
            f"  事件 {result.events} 条（其中 checkpoint {result.checkpoint_events} 条，"
            f"最后一个 checkpoint seq={result.checkpoint_seq}）"
        )
        for table, rows in result.rows.items():
            print(f"  {table:28s} {rows} 行")

    if not args.verify:
        return 0

    differences = diff(source, result.target)
    failed = {name: item for name, item in differences.items() if not item.ok}
    if args.json:
        print(
            json.dumps(
                {"diff": {name: item.as_dict() for name, item in differences.items()}},
                ensure_ascii=False,
                indent=2,
            )
        )
    else:
        print("\n逐表 diff：")
        for name, item in differences.items():
            if item.ok:
                print(f"  OK   {name:28s} rows={item.source_rows}")
            else:
                print(
                    f"  DIFF {name:28s} source={item.source_rows} target={item.target_rows}"
                    f" missing={len(item.missing_in_target)} extra={len(item.extra_in_target)}"
                    f" changed={len(item.changed)}"
                )
                for key, columns in list(item.changed.items())[:5]:
                    print(f"       {key}: {', '.join(columns)}")
                for key in item.missing_in_target[:5]:
                    print(f"       missing {key}")
                for key in item.extra_in_target[:5]:
                    print(f"       extra   {key}")
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
