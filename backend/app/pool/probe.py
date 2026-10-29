"""并行探测各家 CLI 都有什么模型，以及测某一个模型的连通性。

为什么另开一份「探测」而不是直接读 `service.rosters()`：

* `rosters()` 是**串行**的 —— 一个个 spawn 各家 CLI 的清单命令，最慢的那家决定
  整页要等多久（本机实测 7~20 秒）。人要的是"探完哪家就先看到哪家"，所以这里按 CLI
  开线程并行跑，每探完一家就把结果写进共享状态，界面轮询就能一格一格地长出来。
* 探测结果**不缓存**（每次都真的问一遍）：人点「刷新」就是要看现在的样子。
  CLI 清单不花额度，只花一次进程。

测连通性是另一件事：它会**真的调用一次模型**（花一次额度），所以必须由人点名一个
模型才做，而且只发一句话、只让它回几个字。走的是池子对外那条**现成**的服务路径
（`PoolService.chat_completions`），不另造一条 —— 一条路径才有可比的结论。
"""

from __future__ import annotations

import threading
import time
from typing import Any

from app.pool.cli_model import CliModelSource
from app.pool.service import PoolError
from app.pool.sources import (
    _merge_slots,
    _read_json,
    cli_roster,
    declared_cli_slots,
    invalidate_rosters,
    pool_config_path,
)

#: 一份探测（平台是单实例的，同时只留最近一次）。
_STATE: dict[str, Any] = {}
_LOCK = threading.Lock()


def _slots_with_roster() -> list[dict[str, Any]]:
    """能自报名册的那些槽（`roster` 声明齐全的）。

    同一个 CLI 可能有多条槽（一家一个模型），名册是这家 CLI 的属性，所以按 CLI 名
    归并 —— 探测的单位是"这一家"，不是"这一条"。
    """
    path = pool_config_path()
    document = _read_json(path) if path.exists() else {}
    declared: list[dict[str, Any]] = []
    if isinstance(document, dict):
        slots = document.get("cli_slots")
        if isinstance(slots, list):
            declared = [slot for slot in slots if isinstance(slot, dict)]
    elif declared_cli_slots():
        declared = declared_cli_slots()
    seen: dict[str, dict[str, Any]] = {}
    for slot in _merge_slots(declared):
        if not isinstance(slot.get("roster"), dict):
            continue
        name = str(slot.get("name") or "")
        if not name or name in seen:
            continue
        seen[name] = slot
    return list(seen.values())


def _probe_one(slot: dict[str, Any]) -> dict[str, Any]:
    """跑一家 CLI 的清单命令，返回它的模型名（失败就带上原因）。"""
    name = str(slot.get("name") or "cli-slot")
    started = time.monotonic()
    source = CliModelSource(
        name=name,
        model=str(slot.get("model") or ""),
        binary=str(slot.get("binary") or ""),
        args=tuple(str(part) for part in (slot.get("args") or ())),
    )
    try:
        names = cli_roster(slot, source, None)
    except Exception as error:  # noqa: BLE001 - 一家 CLI 挂了不该拖垮整轮探测
        return {
            "cli": name,
            "status": "failed",
            "models": [],
            "error": f"{type(error).__name__}: {error}",
            "seconds": round(time.monotonic() - started, 1),
        }
    if not names:
        return {
            "cli": name,
            "status": "empty",
            "models": [],
            "error": "这家 CLI 没有列出任何模型（清单命令可能不存在或需要登录）",
            "seconds": round(time.monotonic() - started, 1),
        }
    return {
        "cli": name,
        "status": "done",
        "models": names,
        "error": "",
        "seconds": round(time.monotonic() - started, 1),
    }


def start_roster_probe() -> dict[str, Any]:
    """开始一轮并行探测；已经有一轮在跑就沿用那一轮。"""
    with _LOCK:
        if _STATE.get("running"):
            return snapshot()
        slots = _slots_with_roster()
        if not slots:
            return {
                "started_at": 0.0,
                "running": False,
                "entries": [],
                "note": "这个池子没有能自报名册的 CLI（只有写死的模型）。",
            }
        invalidate_rosters()
        _STATE.clear()
        _STATE.update(
            {
                "started_at": time.time(),
                "running": True,
                "entries": [
                    {
                        "cli": str(slot.get("name") or "cli-slot"),
                        "status": "pending",
                        "models": [],
                        "error": "",
                        "seconds": 0.0,
                    }
                    for slot in slots
                ],
            }
        )

    def work(slot: dict[str, Any]) -> None:
        result = _probe_one(slot)
        with _LOCK:
            entries = _STATE.get("entries") or []
            for index, entry in enumerate(entries):
                if entry.get("cli") == result["cli"]:
                    entries[index] = result
                    break
            if all(entry.get("status") not in ("pending", "running") for entry in entries):
                _STATE["running"] = False

    # 先把每个都标成 running：人要看到"这几家正在探"，而不是一直 pending。
    with _LOCK:
        for entry in _STATE.get("entries") or []:
            entry["status"] = "running"

    for slot in slots:
        threading.Thread(target=work, args=(slot,), daemon=True).start()
    return snapshot()


def _undeclared_route(service: Any, model_id: str) -> tuple[Any, dict[str, Any]] | None:
    """给「名册里有、但没人定档」的模型造一条**临时**路由，只用来测一次。

    人要先知道这个模型通不通，才谈得上要不要给它定档。所以这里按这家 CLI 现有槽的
    调用约定临时跑一次（模型名换成它），**不写任何声明** —— 定档仍是人的决定
    （档位是账户里的事实，平台不猜）。
    """
    from dataclasses import replace

    from app.pool.service import Route
    from app.pool.sources import CLI, Upstream

    cli_name, _, short = model_id.partition("/")
    if not cli_name or not short:
        return None
    slot = next(
        (item for item in _slots_with_roster() if str(item.get("name") or "") == cli_name),
        None,
    )
    if slot is None:
        return None
    source = CliModelSource(
        name=cli_name,
        model=str(slot.get("model") or ""),
        binary=str(slot.get("binary") or ""),
        args=tuple(str(part) for part in (slot.get("args") or ())),
        prompt_via_stdin=bool(slot.get("prompt_via_stdin")),
        file_arg=(str(slot["file_arg"]) if slot.get("file_arg") else None),
        message_flag=(str(slot["message_flag"]) if slot.get("message_flag") else None),
        timeout_seconds=float(slot.get("timeout_seconds") or 900.0),
        store=str(slot.get("store") or ""),
        catalogue=dict(slot.get("catalogue") or {}),
    )
    # CLI 自己那份写法（`opencode/beam-free` vs 短名）：argv 要拿它去跑。
    raw_model = short
    try:
        for entry in cli_roster(slot, source, None):
            if str(entry).split("/")[-1] == short:
                raw_model = str(entry)
                break
    except Exception:  # noqa: BLE001 - 问不到就按短名跑，失败原因会带回来
        raw_model = short
    upstream = Upstream(
        name=f"{cli_name}:{raw_model}",
        protocol=CLI,
        base_url=f"cli://{raw_model}",
        models=(model_id,),
        cli=replace(source, model=raw_model),
        # 档位未知，所以只用于"测一次"，绝不当成可花的额度：不进池子，也不记账。
        tier="metered",
        note="未定档：直接问一次 CLI（只为测试）",
    )
    return Route(upstream=upstream, model=model_id, upstream_model=raw_model), slot


def test_model(model_id: str, service: Any, *, consumer: str = "") -> dict[str, Any]:
    """测一个模型能不能用：真的调用一次，返回成功或失败原因。

    两条路，按人的认知顺序：
    * 池子里**有**这个模型 → 走池子对外那条现成的服务路径（结论能与真实使用相比）；
    * 池子里**没有**、但这家 CLI 的名册里有 → 直接问一次 CLI（临时路由，不写声明）。
    """
    started = time.monotonic()
    payload = {
        "model": model_id,
        "messages": [{"role": "user", "content": "回复两个字：可用"}],
        "max_tokens": 32,
        "stream": False,
    }
    try:
        answer = service.chat_completions(payload, consumer=consumer)
    except PoolError as error:
        if getattr(error, "code", "") != "relay_model_not_found":
            return {
                "model": model_id,
                "ok": False,
                "seconds": round(time.monotonic() - started, 1),
                "error": str(error),
                "code": getattr(error, "code", "") or "pool_error",
                "declared": True,
            }
        built = _undeclared_route(service, model_id)
        if built is None:
            return {
                "model": model_id,
                "ok": False,
                "seconds": round(time.monotonic() - started, 1),
                "error": str(error),
                "code": getattr(error, "code", "") or "pool_error",
                "declared": True,
            }
        route, _slot = built
        try:
            answer = service._cli_chat(route, payload, consumer=consumer)
        except Exception as cli_error:  # noqa: BLE001 - 失败原因原样回给人
            return {
                "model": model_id,
                "ok": False,
                "seconds": round(time.monotonic() - started, 1),
                "error": str(cli_error),
                "code": getattr(cli_error, "code", "") or "cli_error",
                "declared": False,
            }
        return _answer_of(model_id, started, answer, declared=False)
    except Exception as error:  # noqa: BLE001 - 任何异常都要变成"这条不通"
        return {
            "model": model_id,
            "ok": False,
            "seconds": round(time.monotonic() - started, 1),
            "error": f"{type(error).__name__}: {error}",
            "code": "unexpected_error",
            "declared": True,
        }
    return _answer_of(model_id, started, answer, declared=True)


def _answer_of(model_id: str, started: float, answer: dict[str, Any], *, declared: bool) -> dict[str, Any]:
    choices = answer.get("choices") or []
    text = ""
    if choices and isinstance(choices[0], dict):
        message = choices[0].get("message") or {}
        text = str(message.get("content") or "").strip()
    return {
        "model": model_id,
        "ok": True,
        "seconds": round(time.monotonic() - started, 1),
        "reply": text[:200],
        "usage": answer.get("usage") or {},
        "declared": declared,
    }


def snapshot() -> dict[str, Any]:
    """这一轮探测现在到哪了（界面轮询这个）。"""
    with _LOCK:
        entries = [dict(entry) for entry in (_STATE.get("entries") or [])]
        running = bool(_STATE.get("running"))
        started = float(_STATE.get("started_at") or 0.0)
    done = [entry for entry in entries if entry.get("status") in ("done", "empty", "failed")]
    return {
        "started_at": started,
        "running": running,
        "entries": entries,
        "summary": {
            "total": len(entries),
            "finished": len(done),
            "ok": len([entry for entry in done if entry.get("status") == "done"]),
            "models": sum(len(entry.get("models") or []) for entry in done),
        },
        "note": "" if entries else "还没有开始探测（点「刷新」）。",
    }
