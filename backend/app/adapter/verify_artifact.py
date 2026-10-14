"""Structural evidence only; does not claim task-specific human acceptance."""

import json
import sys
from fnmatch import fnmatchcase
from pathlib import Path


def verify(root: Path, patterns: list[str]) -> bool:
    files = [p for p in root.rglob("*") if p.is_file() and ".git" not in p.parts]
    matched = [
        p for p in files if any(fnmatchcase(p.relative_to(root).as_posix(), x) for x in patterns)
    ]
    return bool(matched) and all(p.stat().st_size > 0 for p in matched)


if __name__ == "__main__":
    result = verify(Path.cwd(), json.loads(sys.argv[1]))
    print(
        json.dumps(
            {"nonempty_scoped_artifact": result, "semantic_acceptance": "pending_human_review"}
        )
    )
    raise SystemExit(0 if result else 1)
