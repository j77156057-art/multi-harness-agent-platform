"""平台体检工具：只读地看一眼窗口、额度与账本。

这个工具回答三个问题，各对应一份「面」：

* **A 窗口面**：现在有几个窗口*真的*能接活？
* **B 额度面**：现在有没有任何一条通道会花按量的钱？
* **C 账本面**：有没有卡住的活？

它只读、零额度：不派单、不调模型、不跑发现逻辑，也不新增第三方依赖（只读
平台已有的数据与代码）。

CLI 契约
--------

::

    python tools/doctor/doctor.py            # 人读报告
    python tools/doctor/doctor.py --json     # 一个 JSON 文档，stdout 不混别的

退出码只有三种：

* ``0`` —— 体检通过：三面都没有发现；
* ``1`` —— 发现问题：至少一项发现；
* ``2`` —— 体检自身跑不起来：读不到平台数据。**2 优先于 1**：只要发生读失败，
  即使别的面有发现也退 2。不能用第四种退出码。

单面读失败时，该面给出「读不到 X」，其余可读的面照常产出；读失败绝不当成
「没有数据」，也不会抹掉其它面的真实发现。

``--json`` 的 schema（本任务是唯一权威定义）
-------------------------------------------

任务 2 的断言与任务 3 的文档都以下面的常量表为准，**不要各写一套**：

* :data:`JSON_TOP_FIELDS` —— 顶层六个键，固定为
  ``ok / findings / windows / budget / ledger / error``。
* :data:`JSON_FACE_FIELDS` —— ``windows / budget / ledger`` 三个子结构的
  字段名与类型。
* :data:`JSON_ITEM_FIELDS` —— ``findings``、``windows.windows``、
  ``budget.models`` 列表项的字段名与类型。

``ok`` 仅当退出码为 0 时为 ``true``；``findings`` 是发现列表；读失败时
``ok=false``、``error`` 非空，其余顶层键仍然存在。

数据口径
--------

* 平台数据一律以 ``app.config.settings.data_dir`` 为唯一根：数据库 =
  ``data_dir/state.db``，池子声明 = ``data_dir/pool/config.json``，用户 key =
  ``data_dir/user-keys/index.json``。不认相对 cwd 的字面路径，也不走
  ``pool_config_path()`` 的 cwd 回退。
* 数据库先复制快照（连同 ``-wal`` / ``-shm``）再以 ``mode=ro`` 打开；
  不使用 ``immutable=1``（它会忽略尚未 checkpoint 的已提交事务）。
  真实 data_dir 零写入，也不留下 ``-wal`` / ``-shm`` 边车。
* 在场判定只有一个阈值：``app.adapter.presence.PRESENCE_FRESH_SECONDS``
  （90 秒）。心跳过期即不在场，不另立第二套判据。
"""

from __future__ import annotations

import contextlib
import json
import shutil
import sqlite3
import sys
import tempfile
from collections import Counter
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

#: 仓库根与本仓库的 backend/ 目录。**基于 __file__**，与调用者 cwd 无关。
_REPO_ROOT = Path(__file__).resolve().parents[2]
_BACKEND_DIR = _REPO_ROOT / "backend"
# 放最前面：别处可能也有一个叫 app 的包，体检必须读本仓库的这一份。
with contextlib.suppress(ValueError):
    sys.path.remove(str(_BACKEND_DIR))
sys.path.insert(0, str(_BACKEND_DIR))

from app.adapter.presence import PRESENCE_FRESH_SECONDS  # noqa: E402
from app.config import settings  # noqa: E402

# ---------------------------------------------------------------------------
# 冻结的 JSON schema：任务 2 的断言与任务 3 的文档都读这里
# ---------------------------------------------------------------------------

SCHEMA_VERSION = 1

#: 顶层键，顺序固定。
JSON_TOP_FIELDS: dict[str, str] = {
    "ok": "bool",
    "findings": "list[dict]",
    "windows": "dict",
    "budget": "dict",
    "ledger": "dict",
    "error": "list[str]",
}

#: 三个面的子结构：字段名 -> 类型名。
JSON_FACE_FIELDS: dict[str, dict[str, str]] = {
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

#: 列表项结构：字段名 -> 类型名。
JSON_ITEM_FIELDS: dict[str, dict[str, str]] = {
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

#: 上述三张表的汇总，供任务 2 / 任务 3 一次读取。
JSON_SCHEMA: dict[str, Any] = {
    "version": SCHEMA_VERSION,
    "top": JSON_TOP_FIELDS,
    "faces": JSON_FACE_FIELDS,
    "items": JSON_ITEM_FIELDS,
}

#: 上报的档位字面量。缺失或未知一律 :data:`TIER_UNDETERMINED`，绝不写成 free。
TIER_FREE = "free"
TIER_PAID = "paid"
TIER_USER = "user"
TIER_UNDETERMINED = "未定档"

#: 池子内部档位 -> 上报档位（metered→paid、reserve/user→user、free→free）。
TIER_MAP: dict[str, str] = {
    "free": TIER_FREE,
    "metered": TIER_PAID,
    "reserve": TIER_USER,
    "user": TIER_USER,
}

#: 窗口不可接活的原因枚举。JSON 里只出现这些代码，人读报告翻成中文。
REASON_STATUS_NOT_ONLINE = "status_not_online"
REASON_REGISTRATION_MISSING = "registration_version_missing"
REASON_NO_CREDENTIAL = "no_credential"
REASON_NOT_LIVE = "not_live"
REASON_MODEL_MISSING = "model_missing"
REASON_MODEL_NOT_IN_POOL = "model_not_in_pool"
REASON_MODEL_TIER_UNDETERMINED = "model_tier_undetermined"

REASON_TEXT: dict[str, str] = {
    REASON_STATUS_NOT_ONLINE: "登记状态不是 online",
    REASON_REGISTRATION_MISSING: "没有当前登记版本",
    REASON_NO_CREDENTIAL: "没有可用的平台凭据",
    REASON_NOT_LIVE: "进程不在场",
    REASON_MODEL_MISSING: "池子模式但没有指定模型",
    REASON_MODEL_NOT_IN_POOL: "指定的模型不在额度池里",
    REASON_MODEL_TIER_UNDETERMINED: "模型档位未定",
}

#: 任务状态里不再需要人处理的几种；账本面据此区分「未处理的失败」。
_TERMINAL_TASK_STATUSES = frozenset({"merged", "cancelled", "abandoned"})

#: 真实 schema 中「待评审」对应的任务状态字面量（以 tasks.status 为准）。
_AWAITING_REVIEW_STATUS = "awaiting_review"
#: 评审被领取后停在的处理中状态；配合 lease_until 判超期。
_CLAIMED_REVIEW_STATUS = "claimed"
#: 尝试失败的真实字面量。
_FAILED_ATTEMPT_STATUS = "failed"

_FACE_LABEL: dict[str, str] = {
    "windows": "窗口面",
    "budget": "额度面",
    "ledger": "账本面",
}


class ReadError(RuntimeError):
    """读不到平台数据。消息必须以「读不到」开头，且不得被当成空数据。"""


@dataclass(slots=True)
class FaceResult:
    """一个面的检查结果。``errors`` 非空即该面读失败（JSON 里 checked=false）。"""

    name: str
    data: dict[str, Any]
    findings: list[dict[str, str]] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# 快照与只读读取
# ---------------------------------------------------------------------------


@contextlib.contextmanager
def _snapshot(db_path: Path) -> Iterator[sqlite3.Connection]:
    """把 state.db（连同 -wal/-shm）复制到临时目录，再以只读打开快照。

    为什么必须复制边车：``immutable=1`` 会忽略 ``-wal`` 里尚未 checkpoint 的
    已提交事务，读出来是过期数据。复制 ``-wal`` / ``-shm`` 后以 ``mode=ro``
    打开，SQLite 会正常回放 WAL，读到最新已提交状态。

    真实 data_dir 只读不写：所有写动作都落在临时快照目录，工具退出时整个
    目录被清掉。
    """
    temporary: tempfile.TemporaryDirectory[str] | None = None
    connection: sqlite3.Connection | None = None
    try:
        if not db_path.is_file():
            raise ReadError(f"读不到 state.db（{db_path}）")
        temporary = tempfile.TemporaryDirectory(prefix="agent-doctor-")
        snapshot_path = Path(temporary.name) / "state.db"
        try:
            shutil.copy2(db_path, snapshot_path)
            # -wal / -shm 是同一个数据库的一部分，必须一起复制；快照目录里的
            # 边车不算真实 data_dir 的写入。
            for suffix in ("-wal", "-shm"):
                sidecar = Path(f"{db_path}{suffix}")
                if sidecar.is_file():
                    shutil.copy2(sidecar, Path(f"{snapshot_path}{suffix}"))
            connection = sqlite3.connect(f"{snapshot_path.as_uri()}?mode=ro", uri=True)
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA query_only = ON")
        except OSError as exc:
            raise ReadError(f"读不到 state.db（{db_path}）：无法建立只读快照（{exc}）") from exc
        except sqlite3.Error as exc:
            raise ReadError(f"读不到 state.db（{db_path}）：无法以只读方式打开（{exc}）") from exc
        yield connection
    finally:
        if connection is not None:
            with contextlib.suppress(sqlite3.Error):
                connection.close()
        if temporary is not None:
            with contextlib.suppress(OSError):
                temporary.cleanup()


def _query(
    connection: sqlite3.Connection, sql: str, table: str, params: tuple[Any, ...] = ()
) -> list[sqlite3.Row]:
    """执行一条只读查询；任何 sqlite 错误都按「读不到这张表」上报。"""
    try:
        return connection.execute(sql, params).fetchall()
    except sqlite3.Error as exc:
        raise ReadError(f"读不到 {table} 表（{exc}）") from exc


def _count(connection: sqlite3.Connection, table: str) -> int:
    rows = _query(connection, f"SELECT COUNT(*) AS n FROM {table}", table=table)
    return int(rows[0]["n"] or 0)


def _parse_stamp(value: Any) -> datetime | None:
    """解析平台写入的 ISO 时间；解析不了就是 ``None``（不猜）。"""
    text = str(value or "").strip()
    if not text:
        return None
    try:
        moment = datetime.fromisoformat(text)
    except (TypeError, ValueError):
        return None
    return moment if moment.tzinfo is not None else moment.replace(tzinfo=UTC)


# ---------------------------------------------------------------------------
# 额度数据：池子声明与用户 key（两者分开读、分开报错）
# ---------------------------------------------------------------------------


def _read_pool_declaration(data_dir: Path) -> list[dict[str, Any]]:
    """读 ``data_dir/pool/config.json`` 的 ``cli_slots``。

    文件不存在 = 没有额外声明（采用 DEFAULT_CLI_SLOTS），不是读失败。
    文件存在但坏 JSON / 结构非法 = 读不到，绝不吞成 ``[]``
    （``declared_cli_slots()`` 会吞，所以这里不用它）。
    """
    path = data_dir / "pool" / "config.json"
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return []
    except (OSError, UnicodeError) as exc:
        raise ReadError(f"读不到池子声明文件（{path}）：{exc}") from exc
    try:
        document = json.loads(raw)
    except ValueError as exc:
        raise ReadError(f"读不到池子声明文件（{path}）：不是合法 JSON（{exc}）") from exc
    if not isinstance(document, dict):
        raise ReadError(f"读不到池子声明文件（{path}）：顶层不是对象")
    slots = document.get("cli_slots")
    if slots is None:
        return []
    if not isinstance(slots, list) or any(not isinstance(slot, dict) for slot in slots):
        raise ReadError(f"读不到池子声明文件（{path}）：cli_slots 结构不合法")
    return [dict(slot) for slot in slots]


def _read_user_key_models() -> list[str]:
    """读用户自己的 key 清单，返回其中声明的模型 id。

    路径以 ``app.pool.user_keys`` 的口径为准；清单坏了按读失败上报。延迟
    import：额度池那一包会连带拉起 httpx 之类的依赖，而窗口面/账本面不该
    因为它起不来就整份体检都没法跑。
    """
    try:
        from app.pool import user_keys as user_keys_store
    except Exception as exc:  # noqa: BLE001 - 起不来就是读不到这份清单
        raise ReadError(
            f"读不到用户 key 清单（app.pool.user_keys 起不来：{type(exc).__name__}: {exc}）"
        ) from exc
    path = user_keys_store.index_path()
    try:
        entries = user_keys_store.entries()
    except (user_keys_store.UserKeyError, UnicodeError) as exc:
        raise ReadError(f"读不到用户 key 清单（{path}）：{exc}") from exc
    models: list[str] = []
    for entry in entries:
        for model in entry.models:
            name = str(model or "").strip()
            if name and name not in models:
                models.append(name)
    return models


def _merged_cli_slots(declared: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """``DEFAULT_CLI_SLOTS`` 加池子声明，合并口径直接复用 ``app.pool.sources``。

    延迟到用的时候再 import：``app.pool.sources`` 会连带拉起池子的 CLI 模块，
    而体检的另外两面不该因为额度面依赖起不来就跟着起不来。
    """
    try:
        from app.pool.sources import _merge_slots
    except Exception as exc:  # noqa: BLE001 - 起不来就是读不到额度池口径
        raise ReadError(f"读不到额度池合并口径（{type(exc).__name__}: {exc}）") from exc
    return list(_merge_slots(declared))


@dataclass(slots=True)
class ModelIndex:
    """B 面使用的模型清单与档位；``tier_of`` 同时认池子 id 与槽位的裸模型名。"""

    order: list[str]
    tiers: dict[str, str]
    aliases: dict[str, str] = field(default_factory=dict)

    def tier_of(self, model_id: str) -> str | None:
        tier = self.tiers.get(model_id)
        if tier is not None:
            return tier
        return self.aliases.get(str(model_id).split("/", 1)[-1])


def _budget_model_index(
    declared: list[dict[str, Any]], user_models: list[str]
) -> ModelIndex:
    """合并后的模型清单与档位。

    * CLI 槽位：``DEFAULT_CLI_SLOTS`` 加上池子声明，按 ``app.pool.sources``
      的合并口径（同名同模型覆盖、``enabled: false`` 移除）；模型 id 与池子
      一致，写成 ``name/model``。
    * 用户 key 的模型：档位是 ``user``（映射后仍是 user）。
    """
    index = ModelIndex([], {})
    for slot in _merged_cli_slots(declared):
        name = str(slot.get("name") or "cli-slot")
        model = str(slot.get("model") or "").strip()
        if not model:
            continue
        bare = model.split("/", 1)[-1]
        model_id = f"{name}/{bare}"
        raw_tier = str(slot.get("tier") or "").strip().lower()
        tier = TIER_MAP.get(raw_tier, TIER_UNDETERMINED)
        if model_id not in index.tiers:
            index.order.append(model_id)
            index.tiers[model_id] = tier
        # 窗口里的 model_id 也可能只写槽位的 model 字段（不带 CLI 前缀）。
        index.aliases.setdefault(bare, tier)
    for model in user_models:
        if model not in index.tiers:
            index.order.append(model)
            index.tiers[model] = TIER_USER
    return index


# ---------------------------------------------------------------------------
# A 窗口面
# ---------------------------------------------------------------------------

_WINDOW_SQL = """
SELECT h.harness_id,
       h.status,
       h.registration_version,
       h.model_mode,
       h.model_id,
       (SELECT r.name FROM harness_registrations r
         WHERE r.harness_id = h.harness_id
           AND r.registration_version = h.registration_version
         ORDER BY r.rowid DESC LIMIT 1) AS name,
       (SELECT COUNT(*) FROM auth_tokens t
         WHERE t.subject_type = 'harness'
           AND t.subject_id = h.harness_id
           AND t.revoked_at IS NULL) AS credential_count
  FROM harnesses h
 WHERE h.removed_at IS NULL
 ORDER BY h.harness_id
"""

_PRESENCE_SQL = "SELECT harness_id, seen_at FROM harness_presence"


def _read_presence(
    connection: sqlite3.Connection, now: datetime
) -> dict[str, dict[str, Any]]:
    """每窗口的在场快照。读失败必须报错，不能像 presence_snapshot() 那样吞成空表。"""
    snapshot: dict[str, dict[str, Any]] = {}
    for row in _query(connection, _PRESENCE_SQL, table="harness_presence"):
        harness_id = str(row["harness_id"] or "")
        if not harness_id:
            continue
        seen = _parse_stamp(row["seen_at"])
        age = None if seen is None else (now - seen).total_seconds()
        snapshot[harness_id] = {
            "seen_at": str(row["seen_at"] or ""),
            "age_seconds": age,
            # 唯一阈值：复用平台常量，不再立第二个。
            "live": age is not None and age <= PRESENCE_FRESH_SECONDS,
        }
    return snapshot


def _check_windows(
    connection: sqlite3.Connection,
    db_error: str | None,
    now: datetime,
    declared: list[dict[str, Any]],
    user_models: list[str],
    dependency_errors: list[str] | None = None,
) -> FaceResult:
    if connection is None:
        raise ReadError(db_error or "读不到 state.db")
    # 模型档位只有「池子模式」的窗口才用得上：CLI 模式用窗口自带的模型，
    # 不该因为池子声明或用户 key 读不到就判这个面体检失败。
    index: ModelIndex | None = None
    presence = _read_presence(connection, now)
    rows = _query(connection, _WINDOW_SQL, table="harnesses")

    windows: list[dict[str, Any]] = []
    zombies: list[str] = []
    findings: list[dict[str, str]] = []
    for row in rows:
        harness_id = str(row["harness_id"] or "")
        name = str(row["name"] or "") or harness_id
        live = bool(presence.get(harness_id, {}).get("live"))
        reasons: list[str] = []
        if str(row["status"] or "") != "online":
            reasons.append(REASON_STATUS_NOT_ONLINE)
        if not str(row["registration_version"] or ""):
            reasons.append(REASON_REGISTRATION_MISSING)
        if int(row["credential_count"] or 0) <= 0:
            reasons.append(REASON_NO_CREDENTIAL)
        if not live:
            reasons.append(REASON_NOT_LIVE)
        model_mode = str(row["model_mode"] or "cli")
        model_id = str(row["model_id"] or "")
        if model_mode == "pool":
            # CLI 模式用窗口自带的模型，model_id 为空不算不可用。
            if not model_id:
                reasons.append(REASON_MODEL_MISSING)
            else:
                if index is None:
                    problems = [error for error in (dependency_errors or []) if error]
                    if problems:
                        raise ReadError("；".join(problems))
                    index = _budget_model_index(declared, user_models)
                tier = index.tier_of(model_id)
                if tier is None:
                    reasons.append(REASON_MODEL_NOT_IN_POOL)
                elif tier == TIER_UNDETERMINED:
                    reasons.append(REASON_MODEL_TIER_UNDETERMINED)
        usable = not reasons
        windows.append(
            {
                "harness_id": harness_id,
                "name": name,
                "live": live,
                "usable": usable,
                "reasons": reasons,
            }
        )
        if not live:
            zombies.append(harness_id)
            findings.append(
                {
                    "face": "windows",
                    "code": "zombie_window",
                    "message": f"窗口 {name} 登记在册但进程不在场",
                }
            )

    usable_count = sum(1 for window in windows if window["usable"])
    if windows and usable_count == 0:
        findings.append(
            {
                "face": "windows",
                "code": "no_usable_window",
                "message": f"登记在册的 {len(windows)} 个窗口没有一个能接活",
            }
        )
    data = {
        "checked": True,
        "usable_count": usable_count,
        "zombies": zombies,
        "windows": windows,
    }
    return FaceResult("windows", data, findings, [])


# ---------------------------------------------------------------------------
# B 额度面
# ---------------------------------------------------------------------------


def _check_budget(
    connection: sqlite3.Connection,
    db_error: str | None,
    declared: list[dict[str, Any]] | None,
    declaration_error: str | None,
    user_models: list[str] | None,
    user_keys_error: str | None,
) -> FaceResult:
    problems = [
        error
        for error in (db_error, declaration_error, user_keys_error)
        if error
    ]
    if connection is None and not db_error:
        problems.append("读不到 state.db")
    if problems:
        raise ReadError("；".join(problems))
    if connection is None:  # pragma: no cover - 上面已经兜住
        raise ReadError(db_error or "读不到 state.db")

    index = _budget_model_index(declared or [], user_models or [])
    rows = _query(
        connection,
        "SELECT harness_id, model_id FROM harnesses"
        " WHERE removed_at IS NULL ORDER BY harness_id",
        table="harnesses",
    )
    owners: dict[str, list[str]] = {}
    for row in rows:
        harness_id = str(row["harness_id"] or "")
        model_id = str(row["model_id"] or "")
        if not model_id:
            continue
        owners.setdefault(model_id, []).append(harness_id)
        if model_id not in index.tiers:
            index.order.append(model_id)
            index.tiers[model_id] = index.tier_of(model_id) or TIER_UNDETERMINED

    models = [
        {
            "id": model_id,
            "tier": index.tiers[model_id],
            "owners": owners.get(model_id, []),
        }
        for model_id in index.order
    ]
    paid_channels = [
        model_id for model_id in index.order if index.tiers[model_id] == TIER_PAID
    ]
    findings = [
        {
            "face": "budget",
            "code": "paid_channel",
            "message": f"模型 {model_id} 的档位是按量计费",
        }
        for model_id in paid_channels
    ]
    data = {"checked": True, "models": models, "paid_channels": paid_channels}
    return FaceResult("budget", data, findings, [])


# ---------------------------------------------------------------------------
# C 账本面
# ---------------------------------------------------------------------------


def _check_ledger(
    connection: sqlite3.Connection, db_error: str | None, now: datetime
) -> FaceResult:
    if connection is None:
        raise ReadError(db_error or "读不到 state.db")

    task_rows = _query(
        connection,
        "SELECT task_id, status, current_attempt_id FROM tasks",
        table="tasks",
    )
    attempt_rows = _query(
        connection,
        "SELECT attempt_id, task_id, status FROM task_attempts",
        table="task_attempts",
    )
    review_rows = _query(
        connection,
        "SELECT status, lease_until FROM reviews",
        table="reviews",
    )
    verification_receipts = _count(connection, "verification_receipts")
    acceptance_receipts = _count(connection, "acceptance_receipts")
    events = _count(connection, "task_events")

    task_status_counts = Counter(str(row["status"] or "") for row in task_rows)
    attempt_status_counts = Counter(str(row["status"] or "") for row in attempt_rows)
    tasks_by_id = {
        str(row["task_id"] or ""): {
            "status": str(row["status"] or ""),
            "current_attempt_id": str(row["current_attempt_id"] or ""),
        }
        for row in task_rows
    }

    awaiting_review = task_status_counts.get(_AWAITING_REVIEW_STATUS, 0)
    abandoned_tasks = task_status_counts.get("abandoned", 0)

    # 超期判据（写死、可复现）：评审已领取（status='claimed'）且
    # lease_until 早于当前时间。reviews 没有 pending/requested 态 —— 它创建即
    # claimed，真正的待评审积压看 tasks.status='awaiting_review'。
    overdue_reviews = 0
    for row in review_rows:
        if str(row["status"] or "") != _CLAIMED_REVIEW_STATUS:
            continue
        lease_until = _parse_stamp(row["lease_until"])
        if lease_until is not None and lease_until < now:
            overdue_reviews += 1

    failed_attempts = 0
    failed_unhandled = 0
    for row in attempt_rows:
        if str(row["status"] or "") != _FAILED_ATTEMPT_STATUS:
            continue
        failed_attempts += 1
        task = tasks_by_id.get(str(row["task_id"] or ""))
        if task is None or task["status"] in _TERMINAL_TASK_STATUSES:
            continue
        if task["current_attempt_id"] == str(row["attempt_id"] or ""):
            failed_unhandled += 1

    # 卡住的活 = 超期未评审积压，或失败/放弃仍未处理。
    stuck = overdue_reviews > 0 or failed_unhandled > 0 or abandoned_tasks > 0

    findings: list[dict[str, str]] = []
    if overdue_reviews:
        findings.append(
            {
                "face": "ledger",
                "code": "overdue_review",
                "message": f"有 {overdue_reviews} 条评审已超期仍未完成",
            }
        )
    if failed_unhandled:
        findings.append(
            {
                "face": "ledger",
                "code": "failed_unhandled",
                "message": f"有 {failed_unhandled} 条失败的尝试仍未处理",
            }
        )
    if abandoned_tasks:
        findings.append(
            {
                "face": "ledger",
                "code": "abandoned_task",
                "message": f"有 {abandoned_tasks} 个任务处于已放弃状态",
            }
        )

    data = {
        "checked": True,
        "task_status_counts": dict(sorted(task_status_counts.items())),
        "attempt_status_counts": dict(sorted(attempt_status_counts.items())),
        "awaiting_review": awaiting_review,
        "overdue_reviews": overdue_reviews,
        "failed_attempts": failed_attempts,
        "failed_unhandled": failed_unhandled,
        "abandoned_tasks": abandoned_tasks,
        "reviews_total": len(review_rows),
        "verification_receipts": verification_receipts,
        "acceptance_receipts": acceptance_receipts,
        "events": events,
        "stuck": stuck,
    }
    return FaceResult("ledger", data, findings, [])


# ---------------------------------------------------------------------------
# 汇总
# ---------------------------------------------------------------------------


def _default_windows() -> dict[str, Any]:
    return {"checked": False, "usable_count": 0, "zombies": [], "windows": []}


def _default_budget() -> dict[str, Any]:
    return {"checked": False, "models": [], "paid_channels": []}


def _default_ledger() -> dict[str, Any]:
    return {
        "checked": False,
        "task_status_counts": {},
        "attempt_status_counts": {},
        "awaiting_review": 0,
        "overdue_reviews": 0,
        "failed_attempts": 0,
        "failed_unhandled": 0,
        "abandoned_tasks": 0,
        "reviews_total": 0,
        "verification_receipts": 0,
        "acceptance_receipts": 0,
        "events": 0,
        "stuck": False,
    }


def _run_face(
    name: str,
    default: Callable[[], dict[str, Any]],
    check: Callable[[], FaceResult],
) -> FaceResult:
    """跑一个面。读失败只影响这个面：返回带 error 的空结构，其余面继续。"""
    try:
        return check()
    except ReadError as exc:
        return FaceResult(name, default(), [], [str(exc)])
    except Exception as exc:  # noqa: BLE001 - 一个面的意外不该吞掉另外两面
        return FaceResult(
            name,
            default(),
            [],
            [f"读不到{_FACE_LABEL[name]}的体检结果（{type(exc).__name__}: {exc}）"],
        )


def _capture(
    read: Callable[[], Any],
) -> tuple[Any, str | None]:
    """读一块数据，失败时留下错误、返回 ``None``（不把它当成空数据）。"""
    try:
        return read(), None
    except ReadError as exc:
        return None, str(exc)
    except Exception as exc:  # noqa: BLE001 - 读失败不能伪装成空数据
        return None, f"读不到数据（{type(exc).__name__}: {exc}）"


def _check() -> tuple[dict[str, Any], list[FaceResult]]:
    data_dir = Path(settings.data_dir)
    now = datetime.now(UTC)

    # 额度面依赖的两份文件分开读、分开记错：任一份读失败都要退 2。
    declared, declaration_error = _capture(lambda: _read_pool_declaration(data_dir))
    user_models, user_keys_error = _capture(_read_user_key_models)

    db_error: str | None = None
    connection: sqlite3.Connection | None = None
    stack = contextlib.ExitStack()
    try:
        try:
            connection = stack.enter_context(_snapshot(data_dir / "state.db"))
        except ReadError as exc:
            db_error = str(exc)
        except Exception as exc:  # noqa: BLE001 - 快照起不来同样按读不到上报
            db_error = f"读不到 state.db（{data_dir / 'state.db'}）：{type(exc).__name__}: {exc}"

        faces = [
            _run_face(
                "windows",
                _default_windows,
                lambda: _check_windows(
                    connection,
                    db_error,
                    now,
                    declared or [],
                    user_models or [],
                    [declaration_error, user_keys_error],
                ),
            ),
            _run_face(
                "budget",
                _default_budget,
                lambda: _check_budget(
                    connection,
                    db_error,
                    declared,
                    declaration_error,
                    user_models,
                    user_keys_error,
                ),
            ),
            _run_face(
                "ledger",
                _default_ledger,
                lambda: _check_ledger(connection, db_error, now),
            ),
        ]
    finally:
        stack.close()

    findings = [finding for face in faces for finding in face.findings]
    errors = [
        f"{_FACE_LABEL[face.name]}：{message}"
        for face in faces
        for message in face.errors
    ]
    ok = not errors and not findings
    payload = {
        "ok": ok,
        "findings": findings,
        "windows": next(face.data for face in faces if face.name == "windows"),
        "budget": next(face.data for face in faces if face.name == "budget"),
        "ledger": next(face.data for face in faces if face.name == "ledger"),
        "error": errors,
    }
    return payload, faces


def _error_payload(message: str) -> dict[str, Any]:
    """连体检自身都没跑起来时，仍然给出一份符合 schema 的单文档。"""
    return {
        "ok": False,
        "findings": [],
        "windows": _default_windows(),
        "budget": _default_budget(),
        "ledger": _default_ledger(),
        "error": [message],
    }


# ---------------------------------------------------------------------------
# 人读报告
# ---------------------------------------------------------------------------

_TIER_TEXT: dict[str, str] = {
    TIER_FREE: "free（免费）",
    TIER_PAID: "paid（按量计费）",
    TIER_USER: "user（用户自备或预留，需点头）",
    TIER_UNDETERMINED: "未定档（不按免费计）",
}


def _window_title(item: dict[str, Any]) -> str:
    """人读报告只摆显示名，不摆内部编号。"""
    name = str(item.get("name") or "")
    harness_id = str(item.get("harness_id") or "")
    if name and name != harness_id:
        return name
    # 没有显示名时只能用它唯一的标识；这不是「摆编号」，是分辨窗口所必需。
    return harness_id or "未命名的窗口"


def _render_windows(face: FaceResult) -> list[str]:
    lines = ["A 窗口面"]
    data = face.data
    if face.errors:
        for message in face.errors:
            lines.append(f"  检查窗口：{message}。")
        lines.append("  结论：本面无法体检（读不到平台数据）。")
    else:
        windows = data.get("windows") or []
        if not windows:
            lines.append("  检查窗口：登记表里没有未删除的窗口。")
        for item in windows:
            presence = "在场" if item.get("live") else "进程不在场"
            verdict = "可接活" if item.get("usable") else "不可接活"
            line = f"  检查窗口 {_window_title(item)}：{presence}，{verdict}"
            reasons = item.get("reasons") or []
            if reasons:
                line += "（" + "；".join(REASON_TEXT.get(reason, reason) for reason in reasons) + "）"
            lines.append(line + "。")
        zombies = data.get("zombies") or []
        if zombies:
            titles = {
                str(item.get("harness_id") or ""): _window_title(item)
                for item in windows
            }
            names = [titles.get(zombie, "未命名的窗口") for zombie in zombies]
            lines.append("  发现僵尸窗口：" + "、".join(names) + "。")
        lines.append(f"  结论：现在有 {int(data.get('usable_count') or 0)} 个窗口真的能接活。")
    lines.append(
        "  本面检查了什么 / 没检查什么：检查了登记状态、进程在场"
        f"（心跳阈值 {int(PRESENCE_FRESH_SECONDS)} 秒，复用平台常量）、"
        "接活条件与模型档位；没检查 CLI 是否安装在本机，也没有实际派单。"
    )
    return lines


def _render_budget(face: FaceResult) -> list[str]:
    lines = ["B 额度面"]
    data = face.data
    if face.errors:
        for message in face.errors:
            lines.append(f"  检查额度：{message}。")
        lines.append("  结论：本面无法体检（读不到平台数据）。")
    else:
        models = data.get("models") or []
        if not models:
            lines.append("  检查模型：额度池里没有任何模型。")
        for model in models:
            tier = str(model.get("tier") or TIER_UNDETERMINED)
            owners = model.get("owners") or []
            owner_text = "、".join(owners) if owners else "（没有窗口在用）"
            lines.append(
                f"  检查模型 {model.get('id')}：档位 {_TIER_TEXT.get(tier, tier)}，"
                f"属主窗口 {owner_text}。"
            )
        paid = data.get("paid_channels") or []
        if paid:
            lines.append(
                f"  结论：现在有 {len(paid)} 条通道会花按量的钱（"
                + "、".join(paid)
                + "）。"
            )
        else:
            undetermined = sum(
                1 for model in models if str(model.get("tier")) == TIER_UNDETERMINED
            )
            note = (
                f"（另有 {undetermined} 条未定档，未计入免费）" if undetermined else ""
            )
            lines.append(f"  结论：现在没有任何一条通道会花按量的钱{note}。")
    lines.append(
        "  本面检查了什么 / 没检查什么：检查了池子声明、用户 key 的档位与窗口在用模型；"
        "没有调用模型、没有探测上游，也没有核对账单。"
    )
    return lines


def _render_ledger(face: FaceResult) -> list[str]:
    lines = ["C 账本面"]
    data = face.data
    if face.errors:
        for message in face.errors:
            lines.append(f"  检查账本：{message}。")
        lines.append("  结论：本面无法体检（读不到平台数据）。")
    else:
        task_counts = data.get("task_status_counts") or {}
        attempt_counts = data.get("attempt_status_counts") or {}
        lines.append(
            "  检查任务：共 "
            f"{sum(task_counts.values())} 条；状态分布 "
            + (
                "、".join(f"{status} {count}" for status, count in task_counts.items())
                or "（无）"
            )
            + "。"
        )
        lines.append(
            "  检查尝试：共 "
            f"{sum(attempt_counts.values())} 条；状态分布 "
            + (
                "、".join(
                    f"{status} {count}" for status, count in attempt_counts.items()
                )
                or "（无）"
            )
            + "。"
        )
        lines.append(
            f"  检查待评审积压：{int(data.get('awaiting_review') or 0)} 条"
            "（判据 tasks.status='awaiting_review'）。"
        )
        lines.append(
            f"  检查超期评审：{int(data.get('overdue_reviews') or 0)} 条"
            "（判据 reviews.status='claimed' 且 lease_until 早于当前时间）。"
        )
        lines.append(
            f"  检查失败与放弃：失败尝试 {int(data.get('failed_attempts') or 0)} 条，"
            f"其中仍未处理 {int(data.get('failed_unhandled') or 0)} 条；"
            f"放弃任务 {int(data.get('abandoned_tasks') or 0)} 条。"
        )
        lines.append(
            f"  检查回执与事件：验证回执 {int(data.get('verification_receipts') or 0)} 条，"
            f"验收回执 {int(data.get('acceptance_receipts') or 0)} 条，"
            f"事件 {int(data.get('events') or 0)} 条。"
        )
        verdict = "有卡住的活" if data.get("stuck") else "没有卡住的活"
        lines.append(f"  结论：账本上{verdict}。")
    lines.append(
        "  本面检查了什么 / 没检查什么：检查了任务、尝试、评审、回执与事件的计数和超期；"
        "没有重放事件，也没有判断某条活该不该继续。"
    )
    return lines


def _render(payload: dict[str, Any], faces: list[FaceResult]) -> str:
    by_name = {face.name: face for face in faces}
    lines = ["平台体检 · 只读报告", ""]
    for name, renderer in (
        ("windows", _render_windows),
        ("budget", _render_budget),
        ("ledger", _render_ledger),
    ):
        face = by_name.get(name)
        if face is None:
            face = FaceResult(name, {}, [], ["读不到体检结果"])
        lines.extend(renderer(face))
        lines.append("")
    if payload.get("error"):
        lines.append("体检结论：体检没能跑完整（读不到平台数据），见上方各面的说明。")
    elif payload.get("findings"):
        lines.append("体检结论：发现问题，见上方各面的结论与发现。")
    else:
        lines.append("体检结论：三面全清。")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------


def _force_utf8_stdio() -> None:
    """把输出固定成 UTF-8；宿主不支持时保持原样，不因编码问题起不来。"""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError, OSError):
            pass


def _exit_code(payload: dict[str, Any]) -> int:
    if payload.get("error"):
        return 2
    if payload.get("findings"):
        return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    """进程内可调用的入口。``--json`` 时 stdout 只输出一个 JSON 文档。"""
    _force_utf8_stdio()
    arguments = list(sys.argv[1:] if argv is None else argv)
    json_mode = "--json" in arguments
    unknown = [argument for argument in arguments if argument != "--json"]
    if unknown:
        message = "未知参数：" + " ".join(unknown)
        if json_mode:
            print(json.dumps(_error_payload(message), ensure_ascii=False))
        else:
            print("用法：python tools/doctor/doctor.py [--json]", file=sys.stderr)
            print(message, file=sys.stderr)
        return 2

    try:
        payload, faces = _check()
    except Exception as exc:  # noqa: BLE001 - 体检自身失败也必须给出合法 JSON
        payload = _error_payload(f"读不到体检结果：{type(exc).__name__}: {exc}")
        faces = []

    if json_mode:
        print(json.dumps(payload, ensure_ascii=False))
    else:
        print(_render(payload, faces))
    return _exit_code(payload)


if __name__ == "__main__":
    raise SystemExit(main())
