"""One scope rule for every layer that judges ``exclusive_paths``.

The formal dispatch API accepts a bare directory as an exclusive path
(``app/adapter/dispatch.py`` only rejects absolute paths, ``.`` prefixes,
``..`` segments, ``:`` and ``\\``).  The two consumers of that spec used
``fnmatchcase`` on the raw pattern, where ``fnmatchcase("src/note.md", "src")``
is ``False`` -- so a member that committed exactly the authorized file inside
``src`` was still refused as out of scope, and the attempt was abandoned.

This module keeps the three accepted spellings working:

* ``docs/x/note.md``  exact file path          -> matches that file only
* ``src``             directory path           -> matches ``src`` and ``src/**``
* ``src/**``, ``*.md`` glob                    -> plain ``fnmatchcase``

It is deliberately dependency-free and importable both as ``app.adapter.scope``
and from the standalone ``verify_artifact.py`` script.
"""

from __future__ import annotations

from fnmatch import fnmatchcase

__all__ = ["path_in_scope", "is_runtime_artifact"]

#: 跑起来就会出现的文件：解释器缓存、测试缓存、日志、覆盖率、编辑器临时文件。
#:
#: 它们不是交付物，也不该进 commit。2026-11-05 的 e16 里，三条 ``.pyc`` 被
#: 当成「越界交付」计入了 9 条越界路径 —— 判定本身没错（契约里确实没写），
#: 但那是一个**运行产物**，把它算进"成员乱写文件"会掩盖真正的问题
#: （``static/index.html`` 才是那条没写进契约的真实产出）。
#:
#: 注意 ``static/index.html`` 不在这个名单里：网页是真实交付物，它该走
#: 「契约没写就提示人」的路径，不能被运行时白名单一起放过。
_RUNTIME_PATTERNS = (
    "*.pyc",
    "*.pyo",
    "*.log",
    "*.tmp",
    "*.swp",
    "*.swo",
    ".coverage",
    ".coverage.*",
    ".DS_Store",
    "Thumbs.db",
)
_RUNTIME_SEGMENTS = frozenset({"__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache"})


def is_runtime_artifact(path: str) -> bool:
    """Whether ``path`` is a by-product of running code, not a deliverable.

    ``path`` is a repository-relative POSIX path. The check is deliberately
    name-based (``fnmatchcase`` on the whole path and on the basename), so
    ``src/__pycache__/mod.cpython-311.pyc`` matches both by its segment and
    by ``*.pyc``.
    """
    candidate = path.replace("\\", "/").strip().strip("/")
    if not candidate:
        return False
    name = candidate.rsplit("/", 1)[-1]
    if any(
        fnmatchcase(candidate, pattern) or fnmatchcase(name, pattern)
        for pattern in _RUNTIME_PATTERNS
    ):
        return True
    return any(segment in _RUNTIME_SEGMENTS for segment in candidate.split("/"))


def path_in_scope(path: str, patterns: list[str]) -> bool:
    """Return True when ``path`` is inside one of the authorized ``patterns``.

    ``path`` is a repository-relative POSIX path (the callers normalize
    backslashes first).  A pattern that names a directory authorizes that
    directory and everything below it, which is what the dispatch form's
    "允许修改路径" field means to the person filling it in.
    """
    candidate = path.replace("\\", "/").strip()
    if not candidate:
        return False
    for pattern in patterns:
        if not isinstance(pattern, str):
            continue
        rule = pattern.replace("\\", "/").strip()
        if not rule:
            continue
        if fnmatchcase(candidate, rule):
            return True
        directory = rule.rstrip("/")
        if directory and (candidate == directory or candidate.startswith(directory + "/")):
            return True
    return False
