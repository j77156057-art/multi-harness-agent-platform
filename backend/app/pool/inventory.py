"""What is **left** in each quota tier.

The pool already knows how much it *spent*; that is a different question from
how much is *left*.  Only some quotas have a balance you can go and ask for,
and the platform must not invent one for the others:

* the **reserve** tier is a subscription -- the MiniMax M Plan the ``mmx`` CLI
  is signed in with -- and the CLI itself can say what is left of it
  (``mmx quota show``).  Both reserve upstreams (the CLI slot and the direct
  Anthropic-compatible endpoint) spend *that one* allowance;
* the **free** tier is a CLI's own free allowance.  Those CLIs do not expose
  a balance, so the honest answer is "there is no balance to ask for; here is
  the usage";
* the **metered** tier is pay-as-you-go.  There is no balance, only a bill --
  and the pool already accounts for the bill.

So the inventory is per tier: how many models sit in it, what was spent from
it, and -- where a balance exists -- what is left, with the source that said
so and when it was asked.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from datetime import UTC, datetime
from typing import Any

#: The order the tiers are shown in: free first, then the reserve, then the
#: paid one.  Same order the routing uses.
TIER_ORDER = ("free", "reserve", "metered")

TIER_LABEL = {
    "free": "免费",
    "reserve": "兜底",
    "metered": "计量",
}

#: What "remaining" means for a tier, and whether there is one to ask for.
TIER_NATURE = {
    "free": {
        "balance": False,
        "note": "各 CLI 不暴露配额接口：免费档没有可查的余额，只有用量。",
    },
    "reserve": {
        "balance": True,
        "note": "订阅额度（MiniMax M Plan）：CLI 自己知道还剩多少，平台不自动动用它。",
    },
    "metered": {
        "balance": False,
        "note": "按量计费：没有余额概念，只有花了多少。",
    },
}

#: How long a quota reading stays fresh.  The reading costs a network round
#: trip, and a plan's remaining does not move by the second.
QUOTA_TTL_SECONDS = 300.0

#: The last reading, kept in the module.
#:
#: The pool service is built per request, so a cache on the instance would
#: never be hit: every read of the pool would ask the CLI again, and the
#: office would wait for a network round trip each time.
_QUOTA_CACHE: dict[str, Any] = {}
_QUOTA_AT = 0.0


def _iso_ms(value: Any) -> str | None:
    """Epoch milliseconds as an ISO timestamp (``None`` if there is none)."""
    try:
        millis = int(value)
    except (TypeError, ValueError):
        return None
    if millis <= 0:
        return None
    return datetime.fromtimestamp(millis / 1000.0, tz=UTC).isoformat()


def read_minimax_quota(*, binary: str = "mmx", timeout: float = 20.0) -> dict[str, Any]:
    """Ask the MiniMax CLI what is left of the M Plan.

    Read-only, and honest about absence: no CLI, no login, no answer, a
    timeout -- each of those is a *known* reason, not an error to hide.  The
    reading is the CLI's own words; the platform does not compute a balance
    out of thin air.
    """
    path = shutil.which(binary)
    if not path:
        return {
            "known": False,
            "source": f"{binary} quota show",
            "reason": f"{binary} 不在这台机器上，问不到订阅额度",
        }
    try:
        completed = subprocess.run(
            [path, "quota", "show", "--output", "json"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
            env={**os.environ, "NO_COLOR": "1"},
        )
    except subprocess.TimeoutExpired:
        return {
            "known": False,
            "source": f"{binary} quota show",
            "reason": f"{binary} quota show 超时（{timeout:.0f} 秒）",
        }
    except OSError as error:
        return {
            "known": False,
            "source": f"{binary} quota show",
            "reason": f"跑不了 {binary} quota show：{error}",
        }
    if completed.returncode != 0:
        return {
            "known": False,
            "source": f"{binary} quota show",
            "reason": f"{binary} quota show 退出了（{completed.returncode}）",
        }
    try:
        document = json.loads(completed.stdout or "{}")
    except ValueError:
        return {
            "known": False,
            "source": f"{binary} quota show",
            "reason": f"{binary} quota show 的输出不是 JSON",
        }
    reading = parse_minimax_quota(document)
    reading["source"] = f"{binary} quota show"
    reading["checked_at"] = datetime.now(UTC).isoformat()
    return reading


def cached_minimax_quota(
    *,
    binary: str = "mmx",
    timeout: float = 20.0,
    ttl: float = QUOTA_TTL_SECONDS,
) -> dict[str, Any]:
    """The reading, kept for a few minutes so it is asked once, not once a request."""
    global _QUOTA_AT
    now = time.monotonic()
    if _QUOTA_AT and _QUOTA_CACHE and now - _QUOTA_AT <= ttl:
        return _QUOTA_CACHE
    reading = read_minimax_quota(binary=binary, timeout=timeout)
    _QUOTA_CACHE.clear()
    _QUOTA_CACHE.update(reading)
    _QUOTA_AT = now
    return reading


def parse_minimax_quota(document: Any) -> dict[str, Any]:
    """What the MiniMax CLI's own quota answer says.

    Split out from the subprocess so the reading can be checked against a
    recorded answer instead of the network.
    """
    if not isinstance(document, dict):
        return {"known": False, "reason": "额度回答不是一个对象"}
    remains = document.get("model_remains")
    if not isinstance(remains, list) or not remains:
        return {"known": False, "reason": "没有报出任何额度（可能没登录）"}
    entries = [entry for entry in remains if isinstance(entry, dict)]
    if not entries:
        return {"known": False, "reason": "报出的额度不是对象"}
    # The plan's overall allowance is the "general" entry; a named model
    # allowance is narrower, so it must not be presented as the whole plan.
    general = next(
        (entry for entry in entries if str(entry.get("model_name") or "") == "general"),
        entries[0],
    )
    return {
        "known": True,
        "interval": {
            "remaining_percent": general.get("current_interval_remaining_percent"),
            "usage_count": general.get("current_interval_usage_count"),
            "total_count": general.get("current_interval_total_count"),
            "resets_at": _iso_ms(general.get("end_time")),
        },
        "weekly": {
            "remaining_percent": general.get("current_weekly_remaining_percent"),
            "usage_count": general.get("current_weekly_usage_count"),
            "total_count": general.get("current_weekly_total_count"),
            "resets_at": _iso_ms(general.get("weekly_end_time")),
        },
    }


def _spend_for(usage: dict[str, Any], names: set[str]) -> dict[str, Any]:
    """The spend of a set of upstreams, out of the pool's own accounting."""
    upstreams = usage.get("upstreams") or {}
    requests = 0
    prompt_tokens = 0
    completion_tokens = 0
    consumers: dict[str, int] = {}
    for name, bucket in upstreams.items():
        if name not in names or not isinstance(bucket, dict):
            continue
        requests += int(bucket.get("requests") or 0)
        prompt_tokens += int(bucket.get("prompt_tokens") or 0)
        completion_tokens += int(bucket.get("completion_tokens") or 0)
        for who, count in (bucket.get("consumers") or {}).items():
            consumers[str(who)] = consumers.get(str(who), 0) + int(count or 0)
    return {
        "requests": requests,
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "consumers": consumers,
    }


def build_inventory(
    *,
    upstreams: list[Any],
    models: list[dict[str, Any]],
    usage: dict[str, Any],
    quota: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """The tier-by-tier inventory: models, spend, and what is left (if known).

    ``quota`` is a reading from :func:`read_minimax_quota`; it applies to the
    **reserve** tier, because that tier *is* the MiniMax subscription.  A tier
    with no balance says so instead of showing a made-up number.
    """
    tier_of: dict[str, str] = {}
    for upstream in upstreams:
        name = str(getattr(upstream, "name", "") or "")
        tier = str(getattr(upstream, "tier", "metered") or "metered")
        if name:
            tier_of[name] = tier
    models_by_tier: dict[str, list[str]] = {tier: [] for tier in TIER_ORDER}
    for model in models:
        tier = str(model.get("tier") or "metered")
        models_by_tier.setdefault(tier, []).append(str(model.get("id") or ""))
    quota = quota or {}
    tiers: list[dict[str, Any]] = []
    for tier in TIER_ORDER:
        names = {name for name, name_tier in tier_of.items() if name_tier == tier}
        nature = TIER_NATURE.get(tier, {"balance": False, "note": ""})
        balance: dict[str, Any] | None = None
        if tier == "reserve" and quota.get("known"):
            balance = {
                "known": True,
                "source": quota.get("source"),
                "checked_at": quota.get("checked_at"),
                "interval": quota.get("interval") or {},
                "weekly": quota.get("weekly") or {},
            }
        elif tier == "reserve" and quota:
            balance = {
                "known": False,
                "source": quota.get("source"),
                "reason": quota.get("reason"),
                "checked_at": quota.get("checked_at"),
            }
        tiers.append(
            {
                "tier": tier,
                "label": TIER_LABEL.get(tier, tier),
                "models": len(models_by_tier.get(tier, [])),
                "upstreams": sorted(names),
                "spend": _spend_for(usage, names),
                "balance": balance,
                "note": nature["note"],
            }
        )
    return {
        "tiers": tiers,
        "checked_at": datetime.now(UTC).isoformat(),
    }
