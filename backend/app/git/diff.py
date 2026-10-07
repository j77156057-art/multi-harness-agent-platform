"""任务差异：从 canonical 对象库按需计算一次 attempt 的 base→head diff。

机制学习自 Buzz kind:40008（`buzz-sdk/src/builders.rs` 的
``build_diff_message``：结构化元数据 + 有界正文 + ``truncated`` 标志；
``buzz-relay`` 的 ``validate_diff_event`` 同口径校验），**代码原生重写**。

与 Buzz 的刻意差异（如实记录，不硬凑）：

* Buzz 把 patch 作为**消息正文**发布会话（60KB 上限）；我们的线程是事件投影
  （ADR-DEV-003 唯一真相源），diff **不落库、不发布**——按需从权威对象库的
  ``base_commit``/``head_commit`` 计算，读了就有、对象不在就如实说不在。
* Buzz 的 ``repo`` tag 要求 http(s) URL；我们没有远端仓库，映射为 canonical
  本地对象库（无 URL 语义）。
* 逐文件 patch、语言标注、PR 号等元数据出范围（见 diff-message 卡）。
"""

from __future__ import annotations

from dataclasses import dataclass

from app.git.repo import CanonicalRepo, run_git

#: Buzz 用 60KB 上限；取同族的 64 KiB（2 的幂，字节级截断好断言）。
PATCH_LIMIT_BYTES = 64 * 1024


@dataclass(frozen=True, slots=True)
class DiffFile:
    path: str
    insertions: int | None
    deletions: int | None
    binary: bool

    def as_json(self) -> dict[str, object]:
        return {
            "path": self.path,
            "insertions": self.insertions,
            "deletions": self.deletions,
            "binary": self.binary,
        }


def parse_numstat(text: str) -> tuple[tuple[DiffFile, ...], int, int]:
    """``git diff --numstat`` -> (files, total insertions, total deletions).

    A binary file shows ``-`` for both counts -- kept as ``None`` + a
    ``binary`` flag rather than an invented zero.
    """
    files: list[DiffFile] = []
    insertions = 0
    deletions = 0
    for line in text.splitlines():
        if not line.strip():
            continue
        parts = line.split("\t")
        if len(parts) < 3:
            continue
        raw_insertions, raw_deletions, path = parts[0], parts[1], "\t".join(parts[2:])
        if raw_insertions == "-" or raw_deletions == "-":
            files.append(DiffFile(path=path, insertions=None, deletions=None, binary=True))
            continue
        try:
            added = int(raw_insertions)
            removed = int(raw_deletions)
        except ValueError:
            continue
        files.append(DiffFile(path=path, insertions=added, deletions=removed, binary=False))
        insertions += added
        deletions += removed
    return tuple(files), insertions, deletions


def truncate_patch(patch: str, limit_bytes: int) -> tuple[str, bool]:
    """Byte-bounded preview; cutting mid-character drops the partial char."""
    encoded = patch.encode("utf-8")
    if len(encoded) <= limit_bytes:
        return patch, False
    return encoded[:limit_bytes].decode("utf-8", errors="ignore"), True


def compute_task_diff(
    repo: CanonicalRepo,
    *,
    base_commit: str,
    head_commit: str,
    include_patch: bool = False,
    patch_max_bytes: int = PATCH_LIMIT_BYTES,
) -> dict[str, object]:
    """Stat always; the patch only on request and only up to ``patch_max_bytes``."""
    numstat = run_git("diff", "--numstat", base_commit, head_commit, cwd=repo.path)
    files, insertions, deletions = parse_numstat(numstat.stdout)
    payload: dict[str, object] = {
        "files": [file.as_json() for file in files],
        "files_changed": len(files),
        "insertions": insertions,
        "deletions": deletions,
        "patch": None,
        "truncated": False,
    }
    if include_patch:
        diff = run_git("diff", base_commit, head_commit, cwd=repo.path)
        patch, truncated = truncate_patch(diff.stdout, patch_max_bytes)
        payload["patch"] = patch
        payload["truncated"] = truncated
    return payload
