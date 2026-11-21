"""名册对账：探查完结后，把「声明」与「CLI 自己的名册」对一遍，撤掉陈旧的声明。

背景（2026-11-21 真机核实）：``codebuddy/hy3`` 在登记里还挂着，但 11-21 凌晨的
名册重探查（``POST /pool/roster-probe``）里 codebuddy 自报的 23 个模型已经没有
它了 —— 名册消失、登记还在，人还能选中一个已死的模型。10-29 的 qoder 断流同因：
``qoder/Qwen3.8-Flash`` 通道死了但登记一直在。

保守优先的三条规则：

* 只有**探查成功**（``status == "done"`` 且 ``error`` 为空）的那一轮名册才可信，
  缺席才计数。探查失败 ≠ 模型没了 —— 失败的那一家**不参与对账**，而且把该家
  的连续缺席计数**清零**（连续性被打破，重新数；否则两次失败夹一次缺席就会被
  误当成「连续两轮缺席」）。
* **连续缺席 ≥ 2 轮**才自动撤声明（``ABSENT_ROUNDS_TO_RETRACT``）；只缺 1 轮
  只标 stale（``missing_from_roster`` + ``absent_rounds``），绝不动。
* 撤销留审计：撤了什么、何时、依据哪两轮探查，落盘到对账状态文件
  （``data/pool/reconcile.json``，与池子声明同一目录、重启不丢），
  ``/pool/status`` 的 ``retracted`` 段可见。

声明被撤后，窗口的 model_selection **不动**（路由失败走既有的 evidence/弹窗
链路，平台不替人改选择）；``/pool/status`` 里只**报告**哪些窗口指向了已撤或
陈旧的模型（:func:`stale_selections`）。

对账的幂等性：每一轮探查以它的 ``started_at`` 为身份（round id），同一个
round id 只记一次账 —— 重复跑同一轮不重复计数、不重复撤。
"""

from __future__ import annotations

import json
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.config import settings
from app.pool.sources import (
    _CONFIG_LOCK,
    PoolDeclarationError,
    _merge_slots,
    _read_declarations,
    _write_declarations,
    pool_config_path,
    pool_config_write_path,
)

#: 连续缺席多少轮才自动撤声明。**不许改小**：一轮的缺席可能只是那家 CLI 这次
#: 没登录、清单命令抽风 —— 两轮连续缺席才敢下「这个模型没了」的结论。
ABSENT_ROUNDS_TO_RETRACT = 2

#: 审计最多留多少条：状态文件不无限长，撤档的事故复盘看最近这些足够。
AUDIT_LIMIT = 200

#: 幂等标记最多留多少个：探查轮是单实例串行的，最近这些轮足够判重。
ROUND_MARKERS_LIMIT = 20

#: 对账状态自己的读写锁（与声明文件的 ``_CONFIG_LOCK`` 是两把：各管各的文件）。
_STATE_LOCK = threading.Lock()


def reconcile_state_path() -> Path:
    """对账状态落在哪里：与池子声明同一个目录（``AP_DATA_DIR/pool``）。"""
    return Path(settings.data_dir) / "pool" / "reconcile.json"


def _read_state(path: Path) -> dict[str, Any]:
    """读对账状态。文件不在 = 还没对过账；读不动/坏了 = 按没有记录处理。

    坏了不能当成「名册对了」也不能当成「全缺席」：宁可丢计数（少撤、不误撤），
    也不在坏状态上继续累加 —— 审计段丢了可惜，但保守优先。
    """
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, ValueError):
        return {}
    if not isinstance(document, dict):
        return {}
    return document


def _write_state(path: Path, payload: dict[str, Any]) -> None:
    """写对账状态：先写临时文件再原子替换（写一半的崩溃不留半截文件）。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _short(model: str) -> str:
    """模型名的短名口径：``opencode/beam-free`` → ``beam-free``。

    与 ``declare_cli_model`` / ``withdraw_cli_model`` 同款 —— 名册里是 CLI 自己
    的写法，声明里可能是短名也可能不是，比的时候只用最后一段。
    """
    return str(model or "").strip().split("/")[-1]


def _model_id(cli: str, model: str) -> str:
    """这条声明在池子里的模型 id（``codebuddy/hy3``），对账与审计都用它。"""
    return f"{cli}/{_short(model)}"


def _live_slots(config_path: Path | None = None) -> list[dict[str, Any]]:
    """现在**活着**的声明（内置 + 人写的合并后的那份），与池子提供的口径一致。

    声明文件读不动时按「没有声明」处理（fail-closed，与 ``discover_cli_slots``
    同款）：对账撤的是声明，读不出声明就无可对、更无可撤。
    """
    path = config_path or pool_config_path()
    declared: list[dict[str, Any]] = []
    try:
        document = _read_declarations(path)
    except PoolDeclarationError:
        document = {}
    slots = document.get("cli_slots")
    if isinstance(slots, list):
        declared = [slot for slot in slots if isinstance(slot, dict)]
    return [slot for slot in _merge_slots(declared) if str(slot.get("model") or "").strip()]


def _trusted_rosters(entries: list[dict[str, Any]]) -> dict[str, set[str]]:
    """探查**成功**的那些槽：``cli → 名册短名集合``。

    只有 ``status == "done"`` 且 ``error`` 为空的槽可信；``empty`` / ``failed``
    都不算 —— 「这家 CLI 没列出任何模型」与「探查失败」都不是「名册缺席」的
    证据，拿它们计数就是在给误撤开门。
    """
    trusted: dict[str, set[str]] = {}
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        if str(entry.get("status") or "") != "done":
            continue
        if str(entry.get("error") or "").strip():
            continue
        cli = str(entry.get("cli") or "").strip()
        if not cli:
            continue
        trusted[cli] = {_short(str(name)) for name in (entry.get("models") or [])}
    return trusted


def _probed_clis(entries: list[dict[str, Any]]) -> set[str]:
    """这一轮**探过**的 cli（包括失败的）—— 没探过的家不参与对账。"""
    out: set[str] = set()
    for entry in entries:
        if isinstance(entry, dict):
            cli = str(entry.get("cli") or "").strip()
            if cli:
                out.add(cli)
    return out


def _retract_slot(slot: dict[str, Any], *, config_path: Path | None = None) -> bool:
    """把一条声明从声明存储里移除（自动撤档）。已不在了返回 ``False``。

    人写的那条直接删（与 ``withdraw_cli_model`` 同款口径：按 cli + 短名比对）；
    内置的那条文件里没有可删的东西，按既有约定写一条 ``{"enabled": false}`` 的
    覆盖 —— ``_merge_slots`` 由此把它拿掉（这就是「整条 CLI 关掉」的既有写法，
    只不过这里关的是一条）。
    """
    cli = str(slot.get("name") or "").strip()
    model = str(slot.get("model") or "").strip()
    if not cli or not _short(model):
        return False
    path = config_path or pool_config_write_path()
    with _CONFIG_LOCK:
        fresh = _read_declarations(path)
        raw = fresh.get("cli_slots")
        current = [
            entry for entry in (raw if isinstance(raw, list) else []) if isinstance(entry, dict)
        ]
        kept = [
            entry
            for entry in current
            if not (
                str(entry.get("name") or "").strip() == cli
                and _short(str(entry.get("model") or "")) == _short(model)
            )
        ]
        if len(kept) == len(current):
            # 文件里没有这条：它是内置槽，写「关闭」的覆盖。
            kept = [*current, {"name": cli, "model": model, "enabled": False}]
        payload = dict(fresh)
        payload["cli_slots"] = kept
        _write_declarations(path, payload)
    return True


def reconcile_round(
    entries: list[dict[str, Any]],
    *,
    round_id: float | str | None = None,
    config_path: Path | None = None,
    state_path: Path | None = None,
) -> dict[str, Any]:
    """对一轮**已完结**的探查做对账：缺席计数、撤陈旧声明、留审计。

    ``entries`` 是探测快照里的那一轮槽结果（每条 ``{cli, status, models, error}``）。
    ``round_id`` 是这一轮的身份（探测用 ``started_at``），重复对同一轮对账是
    **幂等**的：同一个 round id 只记一次账。

    返回这轮对账的报告；对账出的任何异常都不往外抛（调用方是探测的收尾路径，
    对账坏了不能拖垮探测本身，更不能因此撤任何声明）。
    """
    marker = str(round_id if round_id is not None else _now())
    path = state_path or reconcile_state_path()
    try:
        with _STATE_LOCK:
            state = _read_state(path)
            markers = state.get("rounds") if isinstance(state.get("rounds"), list) else []
            if marker in markers:
                return _report_of(
                    entries,
                    marker,
                    retracted=[],
                    absences=_absences_of(state),
                    repeated=True,
                )
            trusted = _trusted_rosters(entries)
            probed = _probed_clis(entries)
            slots = _live_slots(config_path)
            now = _now()
            absences = _absences_of(state)
            retracted: list[dict[str, Any]] = []

            # 1) 探查**失败**的那些家：连续性被打破，缺席计数清零。
            #    失败 ≠ 模型没了 —— 让失败那轮计入缺席就是在给误撤开门。
            for cli in sorted(probed - set(trusted)):
                for slot in slots:
                    if str(slot.get("name") or "") != cli:
                        continue
                    mid = _model_id(cli, str(slot.get("model") or ""))
                    absences.pop(mid, None)

            # 2) 探查**成功**的那些家：名册里缺席的记一次，在册的清零。
            for cli in sorted(trusted):
                roster = trusted[cli]
                for slot in slots:
                    if str(slot.get("name") or "") != cli:
                        continue
                    model = str(slot.get("model") or "").strip()
                    mid = _model_id(cli, model)
                    if _short(model) in roster:
                        absences.pop(mid, None)
                        continue
                    record = absences.get(mid)
                    if not isinstance(record, dict):
                        record = {"count": 0, "since": now, "rounds": []}
                    record["count"] = int(record.get("count") or 0) + 1
                    record["rounds"] = [*_rounds_of(record), marker][-ABSENT_ROUNDS_TO_RETRACT:]
                    record["updated_at"] = now
                    absences[mid] = record
                    if record["count"] >= ABSENT_ROUNDS_TO_RETRACT:
                        retracted.append(
                            _withdraw(
                                slot,
                                record,
                                config_path=config_path,
                                retracted_at=now,
                            )
                        )
                        absences.pop(mid, None)

            payload = {
                "absences": absences,
                "retracted": [
                    *(state.get("retracted") if isinstance(state.get("retracted"), list) else []),
                    *retracted,
                ][-AUDIT_LIMIT:],
                "rounds": [*(markers or []), marker][-ROUND_MARKERS_LIMIT:],
                "updated_at": now,
            }
            _write_state(path, payload)
            return _report_of(entries, marker, retracted=retracted, absences=absences)
    except Exception as error:  # noqa: BLE001 - 对账失败只报告，绝不撤任何声明
        return {
            "round_id": marker,
            "error": f"{type(error).__name__}: {error}",
            "retracted": [],
        }


def _rounds_of(record: dict[str, Any]) -> list[str]:
    """这条缺席记录里留存的探查轮身份（最多 ``ABSENT_ROUNDS_TO_RETRACT`` 个）。"""
    rounds = record.get("rounds")
    if not isinstance(rounds, list):
        return []
    return [str(item) for item in rounds]


def _absences_of(state: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """状态里的缺席计数（只留形状对的：``model_id → {count, since, rounds}``）。"""
    absences = state.get("absences")
    if not isinstance(absences, dict):
        return {}
    return {str(mid): rec for mid, rec in absences.items() if isinstance(rec, dict)}


def _withdraw(
    slot: dict[str, Any],
    record: dict[str, Any],
    *,
    config_path: Path | None,
    retracted_at: str,
) -> dict[str, Any]:
    """撤一条声明并生成它的审计条目。撤不动（文件坏了）就**只记录，不硬撤**。"""
    cli = str(slot.get("name") or "").strip()
    model = str(slot.get("model") or "").strip()
    audit: dict[str, Any] = {
        "cli": cli,
        "model": model,
        "model_id": _model_id(cli, model),
        "retracted_at": retracted_at,
        "absent_rounds": int(record.get("count") or 0),
        "rounds": _rounds_of(record),
        "reason": (
            f"连续 {record.get('count')} 轮名册探查缺席"
            "（CLI 自报名册里没有它；连续缺席按保守规则自动撤档）"
        ),
    }
    try:
        audit["removed"] = bool(_retract_slot(slot, config_path=config_path))
    except PoolDeclarationError as error:
        # 声明文件读不动：撤不了。**不硬撤** —— 保留声明比损坏声明文件安全，
        # 这条只在审计里留个「想撤但没撤成」的痕迹，下一轮再试。
        audit["removed"] = False
        audit["note"] = f"声明文件读不动，本轮未撤：{error}"
    return audit


def _report_of(
    entries: list[dict[str, Any]],
    marker: str,
    *,
    retracted: list[dict[str, Any]],
    absences: dict[str, dict[str, Any]],
    repeated: bool = False,
) -> dict[str, Any]:
    """这轮对账的报告（探测快照的 ``reconcile`` 段里给人和界面看）。"""
    trusted = _trusted_rosters(entries)
    probed = _probed_clis(entries)
    absent_now = {
        mid: int(rec.get("count") or 0)
        for mid, rec in absences.items()
        if mid.split("/", 1)[0] in probed
    }
    return {
        "round_id": marker,
        "trusted": sorted(trusted),
        "untrusted": sorted(probed - set(trusted)),
        "absent": absent_now,
        "retracted": retracted,
        "repeated": repeated,
    }


def stale_declarations(state_path: Path | None = None) -> dict[str, dict[str, Any]]:
    """现在**缺席中**（还没被撤）的声明：``model_id → 缺席标注``。

    ``/pool/status`` 的 ``declarations`` 段据此给陈旧声明加
    ``missing_from_roster: true`` + ``absent_rounds: N`` —— 人和 UI 都看得见，
    但模型仍然可用：缺席一轮不下「死了」的结论。
    """
    state = _read_state(state_path or reconcile_state_path())
    out: dict[str, dict[str, Any]] = {}
    for mid, record in _absences_of(state).items():
        count = int(record.get("count") or 0)
        if count < 1:
            continue
        out[mid] = {
            "missing_from_roster": True,
            "absent_rounds": count,
            "absent_since": str(record.get("since") or ""),
        }
    return out


def retracted_log(state_path: Path | None = None) -> list[dict[str, Any]]:
    """自动撤档的审计：撤了什么、何时、依据哪两轮探查。"""
    state = _read_state(state_path or reconcile_state_path())
    entries = state.get("retracted")
    if not isinstance(entries, list):
        return []
    return [entry for entry in entries if isinstance(entry, dict)]


def stale_selections(database: Any, state_path: Path | None = None) -> list[dict[str, Any]]:
    """哪些窗口的 model_selection 还指向已撤或陈旧的模型（**只报告，不改**）。

    声明被撤后窗口的选择不动：路由失败会走既有的 evidence/弹窗链路，平台不替
    人改选择 —— 但人得能在 ``/pool/status`` 里看见「这个窗口指着一条死模型」。
    数据库读不动就不下结论（这只是一个提示面，不该让它把状态面拖垮）。
    """
    if database is None:
        return []
    try:
        rows = database.read(
            "SELECT harness_id, model_id FROM harnesses"
            " WHERE model_mode = 'pool' AND model_id IS NOT NULL AND removed_at IS NULL"
        )
    except Exception:  # noqa: BLE001 - 读不动就不报告，不添堵
        return []
    gone = {
        str(entry.get("model_id") or "")
        for entry in retracted_log(state_path)
        if isinstance(entry, dict)
    }
    stale = stale_declarations(state_path)
    report: list[dict[str, Any]] = []
    for row in rows:
        try:
            harness = str(row["harness_id"] or "")
            model_id = str(row["model_id"] or "")
        except (KeyError, IndexError, TypeError):
            continue
        if not model_id:
            continue
        if model_id in gone:
            report.append({"harness_id": harness, "model_id": model_id, "state": "retracted"})
        elif model_id in stale:
            report.append(
                {
                    "harness_id": harness,
                    "model_id": model_id,
                    "state": "stale",
                    "absent_rounds": stale[model_id].get("absent_rounds"),
                }
            )
    return report
