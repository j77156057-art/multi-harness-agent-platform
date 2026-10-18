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

__all__ = ["path_in_scope"]


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
