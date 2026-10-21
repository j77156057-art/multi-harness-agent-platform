"""Integration check for a documentation-only canonical repository.

The platform's default integration profile runs the backend test suite. That is
right for the backend repository but exits 5 (``no tests ran``) on a repository
whose only content is Markdown -- and the merge worker treats a non-zero exit as
red, reverts the merge and escalates the task. This profile checks what a docs
repository actually promises:

* ``README.md`` exists;
* every Markdown file is valid UTF-8;
* no Markdown file is empty.

Usage: ``python docs_profile.py [root]`` (default: the current directory).
Exit codes: 0 pass, 1 problems (one line per problem on stderr), 2 usage error.
"""

from __future__ import annotations

import sys
from pathlib import Path

SKIP_DIRS = {".git", ".hg", ".svn", "node_modules", "__pycache__"}


def check(root: Path) -> list[str]:
    problems: list[str] = []
    if not (root / "README.md").is_file():
        problems.append("README.md is missing")
    markdown = 0
    for path in sorted(root.rglob("*.md")):
        relative = path.relative_to(root)
        if any(part in SKIP_DIRS for part in relative.parts):
            continue
        markdown += 1
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            problems.append(f"{relative.as_posix()} is not valid UTF-8")
            continue
        if not text.strip():
            problems.append(f"{relative.as_posix()} is empty")
    if markdown == 0:
        problems.append("no Markdown files in the repository")
    return problems


def main(argv: list[str]) -> int:
    root = Path(argv[1]) if len(argv) > 1 else Path.cwd()
    if not root.is_dir():
        print(f"docs-profile: not a directory: {root}", file=sys.stderr)
        return 2
    problems = check(root)
    if problems:
        for problem in problems:
            print(f"docs-profile: {problem}", file=sys.stderr)
        return 1
    print("docs-profile: ok", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
