"""What is left of a metered quota, in money.

Three different facts, and the platform must not mix them up:

* the **tokens** are the pool's own accounting (it counted them per request);
* the **balance** is the **provider's own answer**, asked over its own
  read-only API;
* the **price** is *not knowable*: the model names in play are the relays'
  own (``gpt-6.1-sol``, ``deepseek-v4-flash[1M]``), so there is no public
  price to apply.  The platform therefore reports tokens and balance and
  says so -- it does not multiply by a number it made up.

A provider's balance endpoint is a **provider fact** -- the same kind of fact
as the protocol shapes the pool already knows -- and the pool asks the
provider itself, read-only.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlsplit

import httpx


def _host(base_url: str) -> str:
    try:
        return (urlsplit(base_url).hostname or "").lower()
    except ValueError:
        return ""


def _origin(base_url: str) -> str:
    parts = urlsplit(base_url)
    if not parts.scheme or not parts.netloc:
        return base_url.rstrip("/")
    return f"{parts.scheme}://{parts.netloc}"


def _read_deepseek(base_url: str, key: str, timeout: float) -> dict[str, Any]:
    """DeepSeek's own balance endpoint (``/user/balance``).

    It answers with the balances per currency; the pool keeps them as they
    come and adds them up only when they share a currency.
    """
    response = httpx.get(
        _origin(base_url) + "/user/balance",
        headers={"Authorization": f"Bearer {key}"},
        timeout=timeout,
    )
    response.raise_for_status()
    return parse_balance_answer(response.json())


def parse_balance_answer(document: Any) -> dict[str, Any]:
    """The provider's own answer, read as it is.

    Split out from the HTTP call so the reading can be checked against a
    recorded answer instead of the network.
    """
    if not isinstance(document, dict):
        return {"known": False, "reason": "余额回答不是一个对象"}
    balances = []
    for info in document.get("balance_infos") or []:
        if not isinstance(info, dict):
            continue
        balances.append(
            {
                "currency": str(info.get("currency") or ""),
                "total": str(info.get("total_balance") or ""),
                "granted": str(info.get("granted_balance") or ""),
                "topped_up": str(info.get("topped_up_balance") or ""),
            }
        )
    if not balances:
        return {"known": False, "reason": "provider 没有报出余额"}
    return {"known": True, "balances": balances, "source": "provider /user/balance"}


def _read_usage_endpoint(base_url: str, key: str, timeout: float) -> dict[str, Any]:
    """The provider's own usage endpoint (``/v1/usage``).

    It answers with the wallet's **remaining**, the **per-day** usage
    (requests, tokens, cost) and the per-model totals.  The daily rows are
    the **account's**, not the platform's: the platform is one of its
    callers, and saying otherwise would be a lie about someone else's bill.
    """
    response = httpx.get(
        _origin(base_url) + "/v1/usage",
        headers={"Authorization": f"Bearer {key}"},
        timeout=timeout,
    )
    response.raise_for_status()
    return parse_usage_answer(response.json())


def parse_usage_answer(document: Any) -> dict[str, Any]:
    """The provider's own usage answer, read as it is."""
    if not isinstance(document, dict):
        return {
            "known": False,
            "reason": "\u7528\u91cf\u56de\u7b54\u4e0d\u662f\u4e00\u4e2a\u5bf9\u8c61",
        }
    remaining = document.get("remaining")
    if remaining is None:
        remaining = document.get("balance")
    daily: list[dict[str, Any]] = []
    for row in document.get("daily_usage") or []:
        if not isinstance(row, dict):
            continue
        daily.append(
            {
                "date": str(row.get("date") or ""),
                "requests": int(row.get("requests") or 0),
                "input_tokens": int(row.get("input_tokens") or 0),
                "output_tokens": int(row.get("output_tokens") or 0),
                "total_tokens": int(row.get("total_tokens") or 0),
                "cost": row.get("cost"),
                "actual_cost": row.get("actual_cost"),
            }
        )
    if remaining is None and not daily:
        return {
            "known": False,
            "reason": "provider \u6ca1\u6709\u62a5\u51fa\u4f59\u989d\u6216\u7528\u91cf",
        }
    return {
        "known": True,
        "remaining": remaining,
        "unit": str(document.get("unit") or ""),
        "daily": daily,
        "source": "provider /v1/usage",
        "scope": "\u8d26\u53f7\u5c42\u9762\u7684\u7528\u91cf\uff08\u5e73\u53f0\u53ea\u662f\u5176\u4e2d\u4e00\u4e2a\u8c03\u7528\u65b9\uff09",
    }


#: The balance endpoints the pool knows how to ask, by provider host.
_READERS = {
    "api.deepseek.com": _read_deepseek,
}

#: ... and by the pool's own upstream naming, for the chains whose provider
#: answers ``/v1/usage`` (the local proxy passes it through and injects the
#: real key, so the pool needs no credential of its own for that hop).
_NAME_READERS = {
    "codex:": _read_usage_endpoint,
}

#: How long a balance reading stays fresh.  Asking the provider is a network
#: round trip, and a balance does not move by the second.
BALANCE_TTL_SECONDS = 300.0

#: The last readings, kept in the module: the pool service is built per
#: request, so a cache on the instance would never be hit.
_BALANCE_CACHE: dict[str, dict[str, Any]] = {}
_BALANCE_AT = 0.0


def cached_balances(
    upstreams: list[Any], *, ttl: float = BALANCE_TTL_SECONDS
) -> dict[str, dict[str, Any]]:
    """The metered providers' balances, asked once and kept for a while."""
    global _BALANCE_AT
    metered = [
        upstream for upstream in upstreams if str(getattr(upstream, "tier", "")) == "metered"
    ]
    if not metered:
        return {}
    now = time.monotonic()
    if _BALANCE_AT and now - _BALANCE_AT <= ttl and _BALANCE_CACHE:
        return {
            upstream.name: _BALANCE_CACHE[upstream.name]
            for upstream in metered
            if upstream.name in _BALANCE_CACHE
        }
    _BALANCE_CACHE.clear()
    for upstream in metered:
        _BALANCE_CACHE[upstream.name] = read_balance(upstream)
    _BALANCE_AT = now
    return {
        upstream.name: _BALANCE_CACHE[upstream.name]
        for upstream in metered
        if upstream.name in _BALANCE_CACHE
    }


def read_balance(upstream: Any, *, timeout: float = 20.0) -> dict[str, Any]:
    """Ask the provider how much is left -- when it can say.

    Every failure mode is a *known reason*, not an error to hide: no
    provider, no credential, a refused call, a timeout.  The caller shows
    the reason next to the tier.
    """
    name = str(getattr(upstream, "name", "") or "")
    base_url = str(getattr(upstream, "base_url", "") or "")
    reader = next(
        (fn for prefix, fn in _NAME_READERS.items() if name.startswith(prefix)), None
    ) or _READERS.get(_host(base_url))
    if reader is None:
        return {"known": False, "upstream": name, "reason": "这个 provider 没有可查的余额接口"}
    try:
        key = upstream.credential_value() or ""
    except Exception:  # a broken credential file is not a crash
        key = ""
    if not key:
        return {"known": False, "upstream": name, "reason": "没有凭据，查不了余额"}
    try:
        result = reader(base_url, key, timeout)
    except httpx.HTTPError as error:
        return {"known": False, "upstream": name, "reason": f"问余额失败：{error}"}
    except ValueError as error:
        return {"known": False, "upstream": name, "reason": f"余额回答读不懂：{error}"}
    result.setdefault("upstream", name)
    result.setdefault("checked_at", datetime.now(UTC).isoformat())
    return result
