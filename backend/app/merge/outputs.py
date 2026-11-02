"""Canonical output registration and dependency hash verification (ADR-056)."""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import subprocess
import uuid
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any

from app.db.connection import Database
from app.git.repo import CanonicalRepo, run_git
from app.spec import EventKind

LARGE_OUTPUT_BYTES = 50 * 1024**2
_SHA256 = re.compile(r"sha256:[0-9a-f]{64}")


class OutputError(RuntimeError):
    """A declared output cannot be proven from the canonical merge tree."""


@dataclass(frozen=True, slots=True)
class OutputRegistration:
    task_id: str
    attempt_id: str
    name: str
    type: str
    schema_version: str
    actual_hash: str
    commit: str


def prepare_output_registrations(
    database: Database,
    repo: CanonicalRepo,
    *,
    task_id: str,
    attempt_id: str,
    merge_commit: str,
) -> tuple[OutputRegistration, ...]:
    """Hash every declared output at the final merge commit, before publishing it."""
    spec = _snapshot_task_spec(database, task_id)
    if spec is None:
        return ()
    declarations = spec.get("outputs", [])
    if not isinstance(declarations, list):
        raise OutputError(f"task {task_id} outputs must be an array")
    registrations: list[OutputRegistration] = []
    for declaration in declarations:
        if not isinstance(declaration, dict):
            raise OutputError(f"task {task_id} has a malformed output declaration")
        name, kind, schema_version = (
            declaration.get("name"),
            declaration.get("type"),
            declaration.get("schema_version"),
        )
        if not all(isinstance(value, str) and value.strip() for value in (name, kind, schema_version)):
            raise OutputError(f"task {task_id} outputs need name, type and schema_version")
        _validate_output_name(name)
        registrations.append(
            OutputRegistration(
                task_id=task_id,
                attempt_id=attempt_id,
                name=name,
                type=kind,
                schema_version=schema_version,
                actual_hash=canonical_output_hash(
                    database,
                    repo,
                    task_id=task_id,
                    attempt_id=attempt_id,
                    name=name,
                    commit=merge_commit,
                ),
                commit=merge_commit,
            )
        )
    names = [item.name for item in registrations]
    if len(names) != len(set(names)):
        raise OutputError(f"task {task_id} declares the same output name twice")
    return tuple(registrations)


def insert_output_registrations(
    conn: sqlite3.Connection,
    registrations: tuple[OutputRegistration, ...],
    *,
    merged_seq: int,
) -> None:
    """Append registry rows in the merge_success transaction.

    **幂等写入**：`task_outputs` 的唯一键是 `(task_id, attempt_id, name)`。如果同一次
    尝试被合并第二次（比如以后给 merge 加重试，或人工重跑一次合并），普通 `INSERT` 会撞
    唯一键、把整笔合并事务回滚 —— 那会让人看到"合并失败"，而实际只是登记行已存在。
    所以这里用 upsert：同一个产出的类型/版本/hash/提交/merged_seq 覆盖成**这一次**合并的
    结果。不删旧行、不改 output_id，避免破坏任何按 id 的引用。

    覆盖而不是"跳过"也是对的：同一次尝试二次合并意味着产出**内容变了**，依赖方要按新
    hash 判；留着旧 hash 等于让下游拿旧账去对。
    """
    for item in registrations:
        conn.execute(
            'INSERT INTO task_outputs(output_id, task_id, attempt_id, name, type,'
            ' schema_version, actual_hash, "commit", merged_seq)'
            ' VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)'
            " ON CONFLICT(task_id, attempt_id, name) DO UPDATE SET"
            " type = excluded.type,"
            " schema_version = excluded.schema_version,"
            " actual_hash = excluded.actual_hash,"
            ' "commit" = excluded."commit",'
            " merged_seq = excluded.merged_seq",
            (
                f"out-{uuid.uuid4().hex}",
                item.task_id,
                item.attempt_id,
                item.name,
                item.type,
                item.schema_version,
                item.actual_hash,
                item.commit,
                merged_seq,
            ),
        )


def dependency_output_satisfied(
    database: Database,
    *,
    parent_task_id: str,
    upstream_task_id: str,
    output_name: str,
    repo: CanonicalRepo | None,
) -> bool:
    """Require an exact snapshot contract and a valid hash from the latest live merge."""
    downstream = _snapshot_task_spec(database, parent_task_id)
    upstream = _snapshot_task_spec(database, upstream_task_id)
    if downstream is None or upstream is None:
        return False
    consumed = _contracts(
        downstream.get("consumes", []),
        key="output",
        output_name=output_name,
        upstream_task_id=upstream_task_id,
    )
    produced = _contracts(upstream.get("outputs", []), key="name", output_name=output_name)
    if len(consumed) != 1 or consumed != produced:
        return False

    success = database.read(
        "SELECT seq FROM task_events WHERE aggregate_id = ? AND event_name = ?"
        " ORDER BY seq DESC LIMIT 1",
        (upstream_task_id, EventKind.MERGE_SUCCESS.value),
    )
    if not success:
        return False
    merged_seq = int(success[0]["seq"])
    if database.read(
        "SELECT 1 FROM task_events WHERE aggregate_id = ? AND event_name = ? AND seq > ?"
        " LIMIT 1",
        (upstream_task_id, "merge_reverted", merged_seq),
    ):
        return False
    rows = database.read(
        'SELECT * FROM task_outputs WHERE task_id = ? AND name = ? AND merged_seq = ?',
        (upstream_task_id, output_name, merged_seq),
    )
    if len(rows) != 1:
        return False
    row = rows[0]
    contract = next(iter(consumed))
    if (str(row["type"]), str(row["schema_version"])) != contract:
        return False
    if not row["actual_hash"] or not row["commit"]:
        return False
    try:
        actual = canonical_output_hash(
            database,
            repo or CanonicalRepo.from_settings(),
            task_id=upstream_task_id,
            attempt_id=str(row["attempt_id"]),
            name=output_name,
            commit=str(row["commit"]),
        )
    except OutputError:
        return False
    return actual == row["actual_hash"]


def canonical_output_hash(
    database: Database,
    repo: CanonicalRepo,
    *,
    task_id: str,
    attempt_id: str,
    name: str,
    commit: str,
) -> str:
    """Hash a file's bytes, or a sorted ``path NUL blob-hash`` directory manifest."""
    _validate_output_name(name)
    object_name = f"{commit}:{name}"
    kind = run_git("cat-file", "-t", object_name, cwd=repo.path, check=False)
    if kind.returncode != 0:
        raise OutputError(f"output {name!r} is absent from canonical commit {commit}")
    if kind.stdout.strip() == "blob":
        size_result = run_git("cat-file", "-s", object_name, cwd=repo.path, check=False)
        try:
            size = int(size_result.stdout.strip())
        except ValueError as exc:
            raise OutputError(f"could not read the size of output {name!r}") from exc
        if size_result.returncode != 0:
            raise OutputError(f"could not read the size of output {name!r}")
        if size > LARGE_OUTPUT_BYTES:
            return _large_artifact_hash(
                database, task_id=task_id, attempt_id=attempt_id, name=name, size=size
            )
        return "sha256:" + hashlib.sha256(_git_bytes(repo, "cat-file", "blob", object_name)).hexdigest()
    if kind.stdout.strip() != "tree":
        raise OutputError(f"output {name!r} is neither a file nor a directory")

    listing = _git_bytes(repo, "ls-tree", "-r", "-z", commit, "--", name)
    entries: list[tuple[bytes, bytes]] = []
    for record in listing.split(b"\0"):
        if not record:
            continue
        try:
            metadata, path = record.split(b"\t", 1)
            _mode, object_type, blob_hash = metadata.split(b" ", 2)
        except ValueError as exc:
            raise OutputError(f"could not read the directory manifest for {name!r}") from exc
        if object_type != b"blob":
            raise OutputError(f"directory output {name!r} contains a non-blob entry")
        entries.append((path, blob_hash))
    manifest = b"".join(path + b"\0" + blob_hash + b"\n" for path, blob_hash in sorted(entries))
    return "sha256:" + hashlib.sha256(manifest).hexdigest()


def _snapshot_task_spec(database: Database, task_id: str) -> dict[str, Any] | None:
    rows = database.read(
        "SELECT ps.content_json FROM tasks t"
        " JOIN plan_snapshots ps ON ps.snapshot_id = t.plan_snapshot_id WHERE t.task_id = ?",
        (task_id,),
    )
    if not rows:
        return None
    try:
        snapshot = json.loads(str(rows[0]["content_json"]))
    except (TypeError, ValueError):
        return None
    if not isinstance(snapshot, dict):
        return None
    for item in snapshot.get("tasks", []):
        if isinstance(item, dict) and str(item.get("id") or "") == task_id:
            return item
    return None


def _contracts(
    declarations: object,
    *,
    key: str,
    output_name: str,
    upstream_task_id: str | None = None,
) -> set[tuple[object, object]]:
    if not isinstance(declarations, list):
        return set()
    return {
        (item.get("type"), item.get("schema_version"))
        for item in declarations
        if isinstance(item, dict)
        and item.get(key) == output_name
        and (upstream_task_id is None or item.get("task") in (None, upstream_task_id))
    }


def _validate_output_name(name: str) -> None:
    path = PurePosixPath(name)
    if (
        not name
        or name == "."
        or name.startswith("/")
        or "\\" in name
        or ":" in name
        or path.as_posix() != name
        or any(part in {".", "..", ""} for part in path.parts)
    ):
        raise OutputError(f"output name must be a normalized repository-relative path: {name!r}")


def _large_artifact_hash(
    database: Database,
    *,
    task_id: str,
    attempt_id: str,
    name: str,
    size: int,
) -> str:
    rows = database.read(
        "SELECT uri, hash FROM artifacts WHERE task_id = ? AND attempt_id = ?"
        " AND size_bytes = ? AND redaction_state = 'clear'",
        (task_id, attempt_id, size),
    )
    for row in rows:
        uri = str(row["uri"]).replace("\\", "/")
        digest = str(row["hash"])
        if (uri == name or uri.endswith("/" + name)) and _SHA256.fullmatch(digest):
            return digest
    raise OutputError(f"large output {name!r} has no matching artifacts content hash")


def _git_bytes(repo: CanonicalRepo, *args: str) -> bytes:
    env = dict(os.environ, GIT_CONFIG_NOSYSTEM="1", GIT_TERMINAL_PROMPT="0")
    try:
        proc = subprocess.run(
            ["git", "-c", "core.quotepath=false", *args],
            cwd=repo.path,
            capture_output=True,
            env=env,
            check=False,
        )
    except OSError as exc:
        raise OutputError(f"could not execute git to hash an output: {exc}") from exc
    if proc.returncode != 0:
        detail = proc.stderr.decode("utf-8", errors="replace").strip()
        raise OutputError(f"could not read the canonical output tree: {detail}")
    return proc.stdout
