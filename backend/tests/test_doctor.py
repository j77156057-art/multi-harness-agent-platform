"""平台体检工具 ``tools/doctor/doctor.py`` 的单元测试。

这份测试只碰 ``tmp_path``：假 SQLite 库、假 ``pool/config.json``、假
``user-keys/index.json``。它直接改 ``app.config.settings`` 的两个属性
（``data_dir`` 与 ``db_path``），因为 ``settings`` 是 import 时构造的进程级
单例，而且 ``db_path`` 不会跟着后来的 ``AP_DATA_DIR`` 再算一次。

工具本身是纯读的：不调模型、不发网络请求、不派单、不启动 worker。所以这里
也只调用 ``doctor.doctor.main([...])``，用 ``capsys`` 读 stdout。

退出码的判据是写死的（见任务 1 的模块 docstring / 常量表）::

    0 = 三面全清
    1 = 至少一项发现
    2 = 读不到平台数据（优先于 1）

JSON schema 也以 ``doctor.doctor`` 里冻结的 ``JSON_TOP_FIELDS`` /
``JSON_FACE_FIELDS`` / ``JSON_ITEM_FIELDS`` 为准；本文件把三张表抄成常量，
这样实现和测试不会“各写一套”。
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

# 必须在 import doctor 之前设置：模块导入会生成 __pycache__。仅在进程内设
# PYTHONDONTWRITEBYTECODE 不足以阻止当前解释器写字节码，必须同时关掉
# sys.dont_write_bytecode（命令行等价物是 python -B）。
os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
sys.dont_write_bytecode = True

from doctor.doctor import main  # noqa: E402

from app.adapter.presence import PRESENCE_FRESH_SECONDS  # noqa: E402
from app.config import settings  # noqa: E402

# ---------------------------------------------------------------------------
# 任务 1 冻结的最小 schema（抄写，不 import 实现里的表来“自证”）
# ---------------------------------------------------------------------------

TOP_FIELDS: dict[str, str] = {
    "ok": "bool",
    "findings": "list[dict]",
    "windows": "dict",
    "budget": "dict",
    "ledger": "dict",
    "error": "list[str]",
}

FACE_FIELDS: dict[str, dict[str, str]] = {
    "windows": {
        "checked": "bool",
        "usable_count": "int",
        "zombies": "list[str]",
        "windows": "list[dict]",
    },
    "budget": {
        "checked": "bool",
        "models": "list[dict]",
        "paid_channels": "list[str]",
    },
    "ledger": {
        "checked": "bool",
        "task_status_counts": "dict[str, int]",
        "attempt_status_counts": "dict[str, int]",
        "awaiting_review": "int",
        "overdue_reviews": "int",
        "failed_attempts": "int",
        "failed_unhandled": "int",
        "abandoned_tasks": "int",
        "reviews_total": "int",
        "verification_receipts": "int",
        "acceptance_receipts": "int",
        "events": "int",
        "stuck": "bool",
    },
}

ITEM_FIELDS: dict[str, dict[str, str]] = {
    "findings": {"face": "str", "code": "str", "message": "str"},
    "windows.windows": {
        "harness_id": "str",
        "name": "str",
        "live": "bool",
        "usable": "bool",
        "reasons": "list[str]",
    },
    "budget.models": {"id": "str", "tier": "str", "owners": "list[str]"},
}

TIER_UNDETERMINED = "未定档"
TIER_FREE = "free"
TIER_PAID = "paid"
TIER_USER = "user"

#: 默认 CLI 槽位里有按量计费的模型。要构造“三面全清”的假数据，就在假
#: config.json 里把这些内置槽位按名字关掉（这是池子声明支持的 ``enabled``）。
DISABLE_BUILTIN_SLOTS: list[dict[str, Any]] = [
    {"name": "opencode-cli", "enabled": False},
    {"name": "codebuddy", "enabled": False},
    {"name": "qoder", "enabled": False},
    {"name": "mmx", "enabled": False},
]


# ---------------------------------------------------------------------------
# 假数据构造
# ---------------------------------------------------------------------------

#: 只列出 doctor 真正会读的列；其余列与本只读面无关。
#: 表名与列名照真实迁移（001 / 006 / 012 / 014 / 017 / 019 / 024 / 030）。
TABLE_DDL: dict[str, str] = {
    "harnesses": """
        CREATE TABLE harnesses (
            harness_id TEXT PRIMARY KEY,
            provider TEXT NOT NULL DEFAULT 'fake',
            adapter TEXT NOT NULL DEFAULT 'fake',
            status TEXT NOT NULL DEFAULT 'offline',
            registration_version TEXT,
            capabilities_json TEXT NOT NULL DEFAULT '[]',
            created_at TEXT NOT NULL DEFAULT '',
            model_mode TEXT NOT NULL DEFAULT 'cli',
            model_id TEXT,
            model_updated_at TEXT,
            model_tier TEXT,
            removed_at TEXT
        )
    """,
    "harness_registrations": """
        CREATE TABLE harness_registrations (
            registration_id TEXT PRIMARY KEY,
            harness_id TEXT NOT NULL,
            registration_version TEXT NOT NULL,
            provider TEXT NOT NULL DEFAULT 'fake',
            adapter TEXT NOT NULL DEFAULT 'fake',
            adapter_version TEXT NOT NULL DEFAULT '1',
            capabilities_json TEXT NOT NULL DEFAULT '[]',
            max_parallel_tasks INTEGER NOT NULL DEFAULT 1,
            status TEXT NOT NULL DEFAULT 'online',
            superseded_by TEXT,
            created_seq INTEGER,
            created_at TEXT NOT NULL DEFAULT '',
            name TEXT,
            registered_by TEXT
        )
    """,
    "auth_tokens": """
        CREATE TABLE auth_tokens (
            token_hash TEXT PRIMARY KEY,
            subject_type TEXT NOT NULL,
            subject_id TEXT NOT NULL,
            label TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL DEFAULT '',
            revoked_at TEXT,
            revoked_reason TEXT
        )
    """,
    "harness_presence": """
        CREATE TABLE harness_presence (
            harness_id TEXT PRIMARY KEY,
            role TEXT,
            started_at TEXT NOT NULL,
            seen_at TEXT NOT NULL
        )
    """,
    "tasks": """
        CREATE TABLE tasks (
            task_id TEXT PRIMARY KEY,
            status TEXT NOT NULL DEFAULT 'draft',
            current_attempt_id TEXT
        )
    """,
    "task_attempts": """
        CREATE TABLE task_attempts (
            attempt_id TEXT PRIMARY KEY,
            task_id TEXT NOT NULL,
            attempt_no INTEGER NOT NULL DEFAULT 1,
            owner TEXT NOT NULL DEFAULT 'fake',
            fencing_epoch INTEGER NOT NULL DEFAULT 1,
            status TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT ''
        )
    """,
    "reviews": """
        CREATE TABLE reviews (
            review_id TEXT PRIMARY KEY,
            task_id TEXT NOT NULL,
            attempt_id TEXT NOT NULL,
            reviewer_harness_id TEXT NOT NULL DEFAULT 'fake',
            reviewer_provider TEXT NOT NULL DEFAULT 'fake',
            status TEXT NOT NULL,
            lease_until TEXT
        )
    """,
    # 这两张表在账本面只被 COUNT(*)：保留真实表名与主键，列不参与判定。
    "verification_receipts": "CREATE TABLE verification_receipts (receipt_id TEXT PRIMARY KEY)",
    "acceptance_receipts": "CREATE TABLE acceptance_receipts (receipt_id TEXT PRIMARY KEY)",
    "task_events": "CREATE TABLE task_events (seq INTEGER PRIMARY KEY AUTOINCREMENT)",
}


def _iso_ago(seconds: float) -> str:
    return (datetime.now(UTC) - timedelta(seconds=seconds)).isoformat()


def _iso_from_now(seconds: float) -> str:
    return (datetime.now(UTC) + timedelta(seconds=seconds)).isoformat()


def create_schema(connection: sqlite3.Connection, *, skip: set[str] | None = None) -> None:
    for table, ddl in TABLE_DDL.items():
        if skip and table in skip:
            continue
        connection.execute(ddl)


def make_db(
    path: Path,
    *,
    skip: set[str] | None = None,
    fill: Any = None,
    wal: bool = False,
) -> sqlite3.Connection | None:
    """建一个 SQLite 假库；``wal=True`` 时返回仍打开的连接（用于未 checkpoint 的 WAL）。"""
    connection = sqlite3.connect(str(path))
    try:
        if wal:
            connection.execute("PRAGMA journal_mode=WAL")
        create_schema(connection, skip=skip)
        if fill is not None:
            fill(connection)
        connection.commit()
    except BaseException:
        connection.close()
        raise
    if wal:
        return connection
    connection.close()
    return None


def add_harness(
    connection: sqlite3.Connection,
    harness_id: str,
    *,
    name: str | None = None,
    status: str = "online",
    registration_version: str | None = "v1",
    model_mode: str = "cli",
    model_id: str = "",
    token: bool = True,
    presence_age: float | None = 5.0,
    removed_at: str | None = None,
) -> None:
    connection.execute(
        "INSERT INTO harnesses"
        " (harness_id, status, registration_version, model_mode, model_id, removed_at)"
        " VALUES (?, ?, ?, ?, ?, ?)",
        (harness_id, status, registration_version, model_mode, model_id, removed_at),
    )
    if registration_version is not None:
        connection.execute(
            "INSERT INTO harness_registrations"
            " (registration_id, harness_id, registration_version, name)"
            " VALUES (?, ?, ?, ?)",
            (f"reg-{harness_id}", harness_id, registration_version, name or harness_id),
        )
    if token:
        connection.execute(
            "INSERT INTO auth_tokens"
            " (token_hash, subject_type, subject_id, label)"
            " VALUES (?, 'harness', ?, '')",
            (f"token-{harness_id}", harness_id),
        )
    if presence_age is not None:
        connection.execute(
            "INSERT INTO harness_presence (harness_id, role, started_at, seen_at)"
            " VALUES (?, 'worker', ?, ?)",
            (harness_id, _iso_ago(3600), _iso_ago(presence_age)),
        )


def add_task(connection: sqlite3.Connection, task_id: str, status: str, current: str = "") -> None:
    connection.execute(
        "INSERT INTO tasks (task_id, status, current_attempt_id) VALUES (?, ?, ?)",
        (task_id, status, current or None),
    )


def add_attempt(connection: sqlite3.Connection, attempt_id: str, task_id: str, status: str) -> None:
    connection.execute(
        "INSERT INTO task_attempts (attempt_id, task_id, status) VALUES (?, ?, ?)",
        (attempt_id, task_id, status),
    )


def add_review(
    connection: sqlite3.Connection, review_id: str, status: str, lease_until: str | None
) -> None:
    connection.execute(
        "INSERT INTO reviews"
        " (review_id, task_id, attempt_id, status, lease_until)"
        " VALUES (?, 't-review', 'a-review', ?, ?)",
        (review_id, status, lease_until),
    )


def write_json(path: Path, document: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(document, ensure_ascii=False, indent=2), encoding="utf-8")


def write_clean_pool(data_dir: Path) -> None:
    """没有内置收费槽位的假池子声明。"""
    write_json(data_dir / "pool" / "config.json", {"cli_slots": DISABLE_BUILTIN_SLOTS})


def write_user_keys(data_dir: Path, keys: list[dict[str, Any]] | None = None) -> None:
    write_json(
        data_dir / "user-keys" / "index.json",
        {"keys": keys or [], "requests": []},
    )


def insert_clean_harness(
    connection: sqlite3.Connection, harness_id: str = "h-clean", **kwargs: Any
) -> None:
    kwargs.setdefault("name", "alpha")
    kwargs.setdefault("presence_age", 5)
    add_harness(connection, harness_id, **kwargs)


def run_main(capsys: pytest.CaptureFixture[str], *args: str) -> tuple[int, str, str]:
    code = main(list(args))
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def run_json(
    capsys: pytest.CaptureFixture[str], *args: str
) -> tuple[int, dict[str, Any], str, str]:
    code, out, err = run_main(capsys, "--json", *args)
    assert out.strip(), f"--json 没有输出 stdout（stderr={err!r}）"
    payload = json.loads(out)
    assert isinstance(payload, dict)
    return code, payload, out, err


# ---------------------------------------------------------------------------
# 结构断言
# ---------------------------------------------------------------------------


def _type_ok(value: Any, type_name: str) -> bool:
    if type_name == "bool":
        return type(value) is bool
    if type_name == "int":
        return type(value) is int
    if type_name == "str":
        return type(value) is str
    if type_name == "dict":
        return type(value) is dict
    if type_name == "list[dict]":
        return type(value) is list and all(type(item) is dict for item in value)
    if type_name == "list[str]":
        return type(value) is list and all(type(item) is str for item in value)
    if type_name == "dict[str, int]":
        return (
            type(value) is dict
            and all(type(key) is str for key in value)
            and all(type(item) is int for item in value.values())
        )
    raise AssertionError(f"未知类型名 {type_name!r}")


def assert_fields(
    document: dict[str, Any],
    expected: dict[str, str],
    *,
    label: str,
    order: bool = True,
) -> None:
    assert type(document) is dict, f"{label} 不是对象"
    assert set(document) == set(expected), (
        f"{label} 字段集合不符：多 {sorted(set(document) - set(expected))}，"
        f"少 {sorted(set(expected) - set(document))}"
    )
    if order:
        assert list(document) == list(expected), f"{label} 字段顺序不符：{list(document)}"
    for field, type_name in expected.items():
        assert _type_ok(
            document[field], type_name
        ), f"{label}.{field} 类型应为 {type_name}，实际 {type(document[field]).__name__}"


def assert_full_schema(payload: dict[str, Any]) -> None:
    assert_fields(payload, TOP_FIELDS, label="顶层")
    for face in ("windows", "budget", "ledger"):
        assert_fields(payload[face], FACE_FIELDS[face], label=face)
    for item in payload["findings"]:
        assert_fields(item, ITEM_FIELDS["findings"], label="findings[]")
    for item in payload["windows"]["windows"]:
        assert_fields(item, ITEM_FIELDS["windows.windows"], label="windows.windows[]")
    for item in payload["budget"]["models"]:
        assert_fields(item, ITEM_FIELDS["budget.models"], label="budget.models[]")


def _tree(root: Path) -> dict[str, tuple[int, int, str]]:
    """tmp_path 内每个文件的 (大小, mtime_ns, sha256)，排除缓存与快照目录。"""
    result: dict[str, tuple[int, int, str]] = {}
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        relative = path.relative_to(root)
        if any(
            part in {"__pycache__", ".pytest_cache"} or part.startswith("agent-doctor-")
            for part in relative.parts
        ):
            continue
        stat = path.stat()
        result[relative.as_posix()] = (
            stat.st_size,
            stat.st_mtime_ns,
            hashlib.sha256(path.read_bytes()).hexdigest(),
        )
    return result


@pytest.fixture
def data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """假 data_dir，并把进程级 settings 的两个属性一起改掉。"""
    directory = tmp_path / "data"
    directory.mkdir()
    monkeypatch.setattr(settings, "data_dir", directory)
    monkeypatch.setattr(settings, "db_path", directory / "state.db")
    assert settings.data_dir == directory
    assert settings.db_path == directory / "state.db"
    return directory


# ---------------------------------------------------------------------------
# 冻结常量
# ---------------------------------------------------------------------------


def test_frozen_schema_tables_match_the_contract() -> None:
    from doctor.doctor import JSON_FACE_FIELDS, JSON_ITEM_FIELDS, JSON_TOP_FIELDS

    assert JSON_TOP_FIELDS == TOP_FIELDS
    assert JSON_FACE_FIELDS == FACE_FIELDS
    assert JSON_ITEM_FIELDS == ITEM_FIELDS


def test_bytecode_writing_is_disabled_explicitly() -> None:
    # 进程内只设环境变量不会影响当前解释器；sys 标志是实际生效的那一半。
    assert os.environ["PYTHONDONTWRITEBYTECODE"] == "1"
    assert sys.dont_write_bytecode is True


def test_presence_threshold_is_the_platform_constant() -> None:
    assert PRESENCE_FRESH_SECONDS == 90


# ---------------------------------------------------------------------------
# 退出码 0 / 1 / 2
# ---------------------------------------------------------------------------


def test_clean_fake_data_exits_zero(data_dir: Path, capsys: pytest.CaptureFixture[str]) -> None:
    write_clean_pool(data_dir)
    write_user_keys(data_dir)
    make_db(settings.db_path, fill=insert_clean_harness)

    code, payload, out, err = run_json(capsys)

    assert code == 0, out + err
    assert payload["ok"] is True
    assert payload["findings"] == []
    assert payload["error"] == []
    assert payload["windows"]["checked"] is True
    assert payload["windows"]["usable_count"] == 1
    assert payload["windows"]["zombies"] == []
    assert payload["budget"]["checked"] is True
    assert payload["budget"]["paid_channels"] == []
    assert payload["ledger"]["checked"] is True
    assert payload["ledger"]["stuck"] is False
    assert payload["ledger"]["task_status_counts"] == {}
    assert_full_schema(payload)


def test_a_single_finding_exits_one(data_dir: Path, capsys: pytest.CaptureFixture[str]) -> None:
    write_clean_pool(data_dir)
    write_user_keys(data_dir)

    def fill(connection: sqlite3.Connection) -> None:
        insert_clean_harness(connection)
        add_task(connection, "t-abandoned", "abandoned")

    make_db(settings.db_path, fill=fill)

    code, payload, _, _ = run_json(capsys)

    assert code == 1
    assert payload["ok"] is False
    assert payload["error"] == []
    assert [finding["code"] for finding in payload["findings"]] == ["abandoned_task"]
    assert payload["ledger"]["abandoned_tasks"] == 1
    assert_full_schema(payload)


def test_missing_state_db_exits_two_with_read_error(
    data_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    write_clean_pool(data_dir)
    write_user_keys(data_dir)

    code, payload, _, _ = run_json(capsys)

    assert code == 2
    assert payload["ok"] is False
    assert payload["findings"] == []
    assert payload["error"], "读失败必须写进 error，而不是空结果"
    assert all("读不到" in message for message in payload["error"])
    assert any("state.db" in message for message in payload["error"])
    # 顶层键仍然齐全；每个面是“未检查”的默认结构，而不是被抹掉。
    assert_full_schema(payload)
    assert payload["windows"]["checked"] is False
    assert payload["budget"]["checked"] is False
    assert payload["ledger"]["checked"] is False
    assert payload["windows"]["windows"] == []
    assert payload["budget"]["models"] == []


def test_bad_json_pool_config_exits_two_but_other_faces_still_report(
    data_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (data_dir / "pool").mkdir(parents=True)
    (data_dir / "pool" / "config.json").write_text("{not json", encoding="utf-8")
    write_user_keys(data_dir)
    make_db(settings.db_path, fill=insert_clean_harness)

    code, payload, _, _ = run_json(capsys)

    assert code == 2
    assert payload["budget"]["checked"] is False
    assert any("读不到" in message and "池子声明" in message for message in payload["error"])
    # 窗口面（CLI 模式不需要池子声明）与账本面照常产出。
    assert payload["windows"]["checked"] is True
    assert payload["windows"]["usable_count"] == 1
    assert payload["ledger"]["checked"] is True
    assert_full_schema(payload)


def test_missing_config_is_not_a_read_failure(
    data_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # 不写 pool/config.json：内置声明仍在，可能产生发现，但绝不该是“读不到”。
    write_user_keys(data_dir)
    make_db(settings.db_path, fill=insert_clean_harness)

    code, payload, _, _ = run_json(capsys)

    assert code != 2
    assert payload["error"] == []
    assert payload["budget"]["checked"] is True
    assert payload["windows"]["checked"] is True
    assert_full_schema(payload)


def test_single_face_read_failure_keeps_other_faces_and_two_beats_one(
    data_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    write_clean_pool(data_dir)
    write_user_keys(data_dir)

    def fill(connection: sqlite3.Connection) -> None:
        # 表被拿掉了，不能再往 harness_presence 插行。
        insert_clean_harness(connection, presence_age=None)
        # 账本面会有一条真实发现；窗口面读失败，所以最终仍是 2（2 优先于 1）。
        add_task(connection, "t-abandoned", "abandoned")

    # 少了 harness_presence 表：只有窗口面读失败，另外两面仍可查。
    make_db(settings.db_path, skip={"harness_presence"}, fill=fill)

    code, payload, _, _ = run_json(capsys)

    assert code == 2
    assert payload["windows"]["checked"] is False
    assert any("harness_presence" in message for message in payload["error"])
    assert payload["budget"]["checked"] is True
    assert payload["ledger"]["checked"] is True
    assert payload["ledger"]["abandoned_tasks"] == 1
    assert "abandoned_task" in {finding["code"] for finding in payload["findings"]}
    assert_full_schema(payload)


@pytest.mark.parametrize(
    ("scenario", "expected_code"),
    [("clean", 0), ("finding", 1), ("read_error", 2)],
)
def test_controlled_fake_data_covers_all_exit_codes(
    data_dir: Path,
    capsys: pytest.CaptureFixture[str],
    scenario: str,
    expected_code: int,
) -> None:
    write_clean_pool(data_dir)
    write_user_keys(data_dir)
    if scenario != "read_error":
        if scenario == "clean":
            make_db(settings.db_path, fill=insert_clean_harness)
        else:

            def fill(connection: sqlite3.Connection) -> None:
                insert_clean_harness(connection)
                add_task(connection, "t-abandoned", "abandoned")

            make_db(settings.db_path, fill=fill)

    code, payload, _, _ = run_json(capsys)

    assert code == expected_code
    assert payload["ok"] is (expected_code == 0)
    if expected_code == 0:
        assert payload["findings"] == []
        assert payload["error"] == []
    elif expected_code == 1:
        assert payload["findings"]
        assert payload["error"] == []
    else:
        assert payload["error"]
        assert all("读不到" in message for message in payload["error"])
    assert_full_schema(payload)


# ---------------------------------------------------------------------------
# A 窗口面
# ---------------------------------------------------------------------------


def test_windows_face_live_usable_reasons_and_single_zombie_criterion(
    data_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    write_json(
        data_dir / "pool" / "config.json",
        {
            "cli_slots": [
                *DISABLE_BUILTIN_SLOTS,
                # 缺 tier：映射为“未定档”，绝不算 free。
                {"name": "ghost", "model": "ghost-model"},
            ]
        },
    )
    write_user_keys(data_dir)

    def fill(connection: sqlite3.Connection) -> None:
        add_harness(connection, "h-live", name="live", presence_age=5)
        # CLI 模式且 model_id 为空是合法状态：用窗口自己 CLI 的模型。
        add_harness(
            connection,
            "h-cli-empty",
            name="cli-empty",
            model_mode="cli",
            model_id="",
            presence_age=5,
        )
        # 心跳超过 90 秒：不在场，进僵尸清单。
        add_harness(connection, "h-stale", name="stale", presence_age=120)
        # 登记的 status 不是 online，但进程还在：不可接活，但不是僵尸。
        add_harness(connection, "h-offline-live", name="offline-live", status="offline")
        add_harness(connection, "h-no-token", name="no-token", token=False)
        add_harness(connection, "h-no-reg", name="no-reg", registration_version=None)
        add_harness(
            connection,
            "h-pool-missing",
            name="pool-missing",
            model_mode="pool",
            model_id="",
        )
        add_harness(
            connection,
            "h-pool-not-in-pool",
            name="pool-not-in-pool",
            model_mode="pool",
            model_id="not-in-pool",
        )
        add_harness(
            connection,
            "h-pool-undetermined",
            name="pool-undetermined",
            model_mode="pool",
            model_id="ghost/ghost-model",
        )
        # 软删窗口：既不出现在 windows[]，也绝不进僵尸清单。
        add_harness(
            connection,
            "h-soft-deleted",
            name="soft-deleted",
            presence_age=None,
            removed_at=_iso_ago(60),
        )

    make_db(settings.db_path, fill=fill)

    code, payload, _, _ = run_json(capsys)

    windows = {item["harness_id"]: item for item in payload["windows"]["windows"]}
    assert set(windows) == {
        "h-live",
        "h-cli-empty",
        "h-stale",
        "h-offline-live",
        "h-no-token",
        "h-no-reg",
        "h-pool-missing",
        "h-pool-not-in-pool",
        "h-pool-undetermined",
    }
    assert "h-soft-deleted" not in windows

    assert windows["h-live"] == {
        "harness_id": "h-live",
        "name": "live",
        "live": True,
        "usable": True,
        "reasons": [],
    }
    assert windows["h-cli-empty"] == {
        "harness_id": "h-cli-empty",
        "name": "cli-empty",
        "live": True,
        "usable": True,
        "reasons": [],
    }
    assert windows["h-stale"]["live"] is False
    assert windows["h-stale"]["usable"] is False
    assert windows["h-stale"]["reasons"] == ["not_live"]
    assert windows["h-offline-live"]["live"] is True
    assert windows["h-offline-live"]["usable"] is False
    assert windows["h-offline-live"]["reasons"] == ["status_not_online"]
    assert windows["h-no-token"]["reasons"] == ["no_credential"]
    assert windows["h-no-reg"]["reasons"] == ["registration_version_missing"]
    assert windows["h-pool-missing"]["reasons"] == ["model_missing"]
    assert windows["h-pool-not-in-pool"]["reasons"] == ["model_not_in_pool"]
    assert windows["h-pool-undetermined"]["reasons"] == ["model_tier_undetermined"]

    # 僵尸只有一个口径：没有 live=true 的记录。软删窗口不进来。
    assert payload["windows"]["zombies"] == ["h-stale"]
    assert set(payload["windows"]["zombies"]) == {
        harness_id for harness_id, item in windows.items() if item["live"] is False
    }
    assert "h-soft-deleted" not in payload["windows"]["zombies"]
    assert payload["windows"]["usable_count"] == 2
    assert code == 1  # 僵尸窗口是一条发现
    assert [finding["code"] for finding in payload["findings"]] == ["zombie_window"]
    assert_full_schema(payload)


# ---------------------------------------------------------------------------
# B 额度面
# ---------------------------------------------------------------------------


def test_budget_tiers_map_unknown_to_undetermined_and_metered_to_paid(
    data_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    write_json(
        data_dir / "pool" / "config.json",
        {
            "cli_slots": [
                *DISABLE_BUILTIN_SLOTS,
                {"name": "freeslot", "model": "free-model", "tier": "free"},
                {"name": "payslot", "model": "costly-model", "tier": "metered"},
                {"name": "reserveslot", "model": "plan-model", "tier": "reserve"},
                {"name": "missingslot", "model": "mystery-model"},
                {"name": "unknownslot", "model": "odd-model", "tier": "unknown-tier"},
            ]
        },
    )
    write_user_keys(
        data_dir,
        [
            {
                "id": "k-user",
                "label": "mine",
                "protocol": "openai_chat",
                "base_url": "https://example.invalid",
                "models": ["user-model"],
                "approval": "always",
            }
        ],
    )

    def fill(connection: sqlite3.Connection) -> None:
        add_harness(
            connection,
            "h-free",
            name="free",
            model_mode="pool",
            model_id="freeslot/free-model",
        )
        add_harness(
            connection,
            "h-paid",
            name="paid",
            model_mode="pool",
            model_id="payslot/costly-model",
        )
        add_harness(
            connection,
            "h-reserve",
            name="reserve",
            model_mode="pool",
            model_id="reserveslot/plan-model",
        )
        add_harness(
            connection,
            "h-user",
            name="user",
            model_mode="pool",
            model_id="user-model",
        )
        add_harness(
            connection,
            "h-missing",
            name="missing",
            model_mode="pool",
            model_id="missingslot/mystery-model",
        )
        add_harness(
            connection,
            "h-unknown",
            name="unknown",
            model_mode="pool",
            model_id="unknownslot/odd-model",
        )

    make_db(settings.db_path, fill=fill)

    code, payload, _, _ = run_json(capsys)

    assert [(model["id"], model["tier"]) for model in payload["budget"]["models"]] == [
        ("freeslot/free-model", TIER_FREE),
        ("payslot/costly-model", TIER_PAID),
        ("reserveslot/plan-model", TIER_USER),
        ("missingslot/mystery-model", TIER_UNDETERMINED),
        ("unknownslot/odd-model", TIER_UNDETERMINED),
        ("user-model", TIER_USER),
    ]
    by_id = {model["id"]: model for model in payload["budget"]["models"]}

    # 缺失/unknown 档位绝不写成 free。
    for model_id in ("missingslot/mystery-model", "unknownslot/odd-model"):
        assert by_id[model_id]["tier"] == TIER_UNDETERMINED
        assert by_id[model_id]["tier"] != TIER_FREE

    # metered 映射为 paid；reserve / user 映射为 user。
    assert by_id["payslot/costly-model"]["tier"] == TIER_PAID
    assert by_id["reserveslot/plan-model"]["tier"] == TIER_USER
    assert by_id["user-model"]["tier"] == TIER_USER
    assert payload["budget"]["paid_channels"] == ["payslot/costly-model"]

    # 属主反查：窗口在用的模型能对上窗口。
    assert by_id["freeslot/free-model"]["owners"] == ["h-free"]
    assert by_id["payslot/costly-model"]["owners"] == ["h-paid"]
    assert by_id["reserveslot/plan-model"]["owners"] == ["h-reserve"]
    assert by_id["user-model"]["owners"] == ["h-user"]
    assert by_id["missingslot/mystery-model"]["owners"] == ["h-missing"]
    assert by_id["unknownslot/odd-model"]["owners"] == ["h-unknown"]

    assert code == 1
    assert [finding["code"] for finding in payload["findings"]] == ["paid_channel"]
    assert_full_schema(payload)


# ---------------------------------------------------------------------------
# C 账本面
# ---------------------------------------------------------------------------


def test_ledger_uses_fixed_overdue_and_stuck_criteria(
    data_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    write_clean_pool(data_dir)
    write_user_keys(data_dir)

    def fill(connection: sqlite3.Connection) -> None:
        # 待评审积压：tasks.status 的真实字面量。
        add_task(connection, "t-awaiting", "awaiting_review")
        add_task(connection, "t-abandoned", "abandoned")
        # 失败尝试是当前 attempt：未处理。
        add_task(connection, "t-failed", "in_progress", "a-failed")
        # 终态任务上的失败尝试：不算未处理。
        add_task(connection, "t-merged", "merged", "a-old")
        add_task(connection, "t-running", "in_progress", "a-running")

        add_attempt(connection, "a-failed", "t-failed", "failed")
        add_attempt(connection, "a-old", "t-merged", "failed")
        add_attempt(connection, "a-running", "t-running", "running")

        # 超期 = reviews.status='claimed' 且 lease_until 早于当前时间。
        add_review(connection, "r-overdue", "claimed", _iso_ago(3600))
        add_review(connection, "r-future", "claimed", _iso_from_now(3600))
        add_review(connection, "r-done", "completed", _iso_ago(3600))

        connection.executemany(
            "INSERT INTO verification_receipts (receipt_id) VALUES (?)",
            [("v1",), ("v2",)],
        )
        connection.execute("INSERT INTO acceptance_receipts (receipt_id) VALUES ('a1')")
        connection.executemany("INSERT INTO task_events DEFAULT VALUES", [(), (), ()])

    make_db(settings.db_path, fill=fill)

    code, payload, _, _ = run_json(capsys)

    ledger = payload["ledger"]
    assert ledger["checked"] is True
    assert ledger["task_status_counts"] == {
        "abandoned": 1,
        "awaiting_review": 1,
        "in_progress": 2,
        "merged": 1,
    }
    assert ledger["attempt_status_counts"] == {"failed": 2, "running": 1}
    assert ledger["awaiting_review"] == 1
    assert ledger["overdue_reviews"] == 1
    assert ledger["failed_attempts"] == 2
    assert ledger["failed_unhandled"] == 1
    assert ledger["abandoned_tasks"] == 1
    assert ledger["reviews_total"] == 3
    assert ledger["verification_receipts"] == 2
    assert ledger["acceptance_receipts"] == 1
    assert ledger["events"] == 3
    assert ledger["stuck"] is True

    assert {finding["code"] for finding in payload["findings"]} == {
        "overdue_review",
        "failed_unhandled",
        "abandoned_task",
    }
    assert code == 1
    assert_full_schema(payload)


def test_ledger_terminal_failures_are_not_stuck(
    data_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    write_clean_pool(data_dir)
    write_user_keys(data_dir)

    def fill(connection: sqlite3.Connection) -> None:
        add_task(connection, "t-merged", "merged", "a-merged")
        add_task(connection, "t-cancelled", "cancelled", "a-cancelled")
        add_attempt(connection, "a-merged", "t-merged", "failed")
        add_attempt(connection, "a-cancelled", "t-cancelled", "failed")

    make_db(settings.db_path, fill=fill)

    code, payload, _, _ = run_json(capsys)

    ledger = payload["ledger"]
    assert ledger["failed_attempts"] == 2
    assert ledger["failed_unhandled"] == 0
    assert ledger["abandoned_tasks"] == 0
    assert ledger["overdue_reviews"] == 0
    assert ledger["stuck"] is False
    assert payload["findings"] == []
    assert code == 0
    assert_full_schema(payload)


# ---------------------------------------------------------------------------
# JSON 文档与人读报告
# ---------------------------------------------------------------------------


def test_json_stdout_is_one_document_with_exact_schema(
    data_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    write_json(
        data_dir / "pool" / "config.json",
        {
            "cli_slots": [
                *DISABLE_BUILTIN_SLOTS,
                {"name": "freeslot", "model": "free-model", "tier": "free"},
                {"name": "payslot", "model": "costly-model", "tier": "metered"},
            ]
        },
    )
    write_user_keys(data_dir)

    def fill(connection: sqlite3.Connection) -> None:
        add_harness(
            connection,
            "h-free",
            name="alpha",
            model_mode="pool",
            model_id="freeslot/free-model",
        )
        add_task(connection, "t-abandoned", "abandoned")

    make_db(settings.db_path, fill=fill)

    code, out, err = run_main(capsys, "--json")

    stripped = out.strip()
    assert stripped, err
    document, end = json.JSONDecoder().raw_decode(stripped)
    assert stripped[end:] == "", f"stdout 里有多余文字：{stripped[end:]!r}"
    assert type(document) is dict
    assert code == 1
    assert document["ok"] is False
    assert document["error"] == []
    assert document["findings"], "有付费通道，findings 不应为空"
    assert document["windows"]["windows"], "有窗口，windows[] 不应为空"
    assert document["budget"]["models"], "有声明，models[] 不应为空"

    # 顶层键与三面子结构、列表项字段名/类型精确对照冻结表。
    assert_full_schema(document)


def test_human_report_has_checked_and_unchecked_lines_without_internal_ids(
    data_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    write_clean_pool(data_dir)
    write_user_keys(data_dir)

    def fill(connection: sqlite3.Connection) -> None:
        add_harness(connection, "h-alpha", name="alpha", presence_age=5)
        add_task(connection, "t-internal-abc", "abandoned")
        add_attempt(connection, "a-internal-def", "t-internal-abc", "failed")
        add_review(connection, "r-internal-ghi", "claimed", _iso_ago(3600))

    make_db(settings.db_path, fill=fill)

    code, out, err = run_main(capsys)

    assert code == 1, out + err
    for header in ("A 窗口面", "B 额度面", "C 账本面"):
        assert header in out

    blocks = re.split(r"(?=^A 窗口面$|^B 额度面$|^C 账本面$)", out, flags=re.MULTILINE)
    blocks = [block for block in blocks if re.match(r"^[ABC] ", block)]
    assert len(blocks) == 3
    for block in blocks:
        assert "本面检查了什么 / 没检查什么" in block
        assert "没检查" in block

    # 人读报告不摆内部编号（任务/尝试/评审/窗口 id）或哈希。
    for internal in ("t-internal-abc", "a-internal-def", "r-internal-ghi", "h-alpha"):
        assert internal not in out
    assert "sha256" not in out.lower()
    assert re.search(r"\b[0-9a-f]{16,}\b", out) is None


# ---------------------------------------------------------------------------
# 零写入与 WAL
# ---------------------------------------------------------------------------


def test_run_writes_nothing_and_leaves_no_wal_or_shm(
    data_dir: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    write_clean_pool(data_dir)
    write_user_keys(data_dir)
    make_db(settings.db_path, fill=insert_clean_harness)

    assert data_dir.is_relative_to(tmp_path)
    before = _tree(tmp_path)

    code, payload, _, _ = run_json(capsys)

    after = _tree(tmp_path)
    assert code == 0
    assert payload["error"] == []
    assert after == before, "体检不应写任何文件（包括真实 data 目录）"
    assert not [path for path in data_dir.rglob("*") if path.name.endswith(("-wal", "-shm"))]
    assert not list(data_dir.glob("state.db-*"))


def test_uncheckpointed_wal_is_read_or_explicitly_reported(
    data_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    write_clean_pool(data_dir)
    write_user_keys(data_dir)

    connection = make_db(
        settings.db_path,
        wal=True,
        fill=lambda conn: add_harness(conn, "h-wal", name="wal", presence_age=5),
    )
    assert connection is not None
    try:
        wal_path = Path(f"{settings.db_path}-wal")
        assert wal_path.is_file(), "测试必须真的留下未 checkpoint 的 -wal"
        assert wal_path.stat().st_size > 0

        code, payload, _, _ = run_json(capsys)

        if code == 2:
            # 平台明确说读不到也算合格：绝不能把过期的空库当“三面全清”。
            assert payload["error"]
            assert any("读不到" in message for message in payload["error"])
        else:
            assert code == 0
            assert payload["error"] == []
            assert [item["harness_id"] for item in payload["windows"]["windows"]] == ["h-wal"]
            assert payload["windows"]["windows"][0]["live"] is True
            assert payload["windows"]["usable_count"] == 1
    finally:
        connection.close()
