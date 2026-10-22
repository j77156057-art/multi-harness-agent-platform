"""A provider's model catalogue, read read-only.

Some providers do not report the tokens of a call; they report a **ratio**
(``context_usage_ratio``: the context used, as a fraction of the window) and
their own unit (``credits``).  That ratio is exact, but turning it into
tokens needs the window -- and the window is in the provider's **model
catalogue**.

So the pool can read the catalogue (a plain GET, no spending) and, when the
catalogue carries the window, derive the tokens exactly:

    tokens = context_usage_ratio * context_window

Both numbers are the provider's own, so the result is the provider's own
number -- not a price the platform made up.

Without a catalogue (or without a credential for it) there is no window, and
the usage stays in the provider's own unit.  That is the honest state, and it
is what the office shows.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import httpx


def read_catalogue(
    *,
    base_url: str,
    path: str,
    credential: str | None,
    timeout: float = 20.0,
) -> dict[str, Any]:
    """Read a provider's model catalogue.

    Read-only, and honest about every failure: no credential, a refused
    call, a timeout, an answer that is not a catalogue.
    """
    if not credential:
        return {"known": False, "reason": "没有凭据，读不了模型目录"}
    url = base_url.rstrip("/") + "/" + path.lstrip("/")
    try:
        response = httpx.get(
            url,
            headers={"Authorization": f"Bearer {credential}", "Accept": "application/json"},
            timeout=timeout,
        )
        response.raise_for_status()
        document = response.json()
    except httpx.HTTPStatusError as error:
        return {"known": False, "reason": f"模型目录被拒了（{error.response.status_code}）"}
    except httpx.HTTPError as error:
        return {"known": False, "reason": f"读模型目录失败：{error}"}
    except ValueError:
        return {"known": False, "reason": "模型目录的回答不是 JSON"}
    windows = parse_windows(document)
    if not windows:
        return {"known": False, "reason": "模型目录里没有 context window"}
    return {"known": True, "models": windows, "source": url}


def parse_windows(document: Any) -> dict[str, int]:
    """``model id -> context window`` from a catalogue, in either shape."""
    if not isinstance(document, dict):
        return {}
    entries: Any = document.get("data")
    if not isinstance(entries, list):
        entries = document.get("models")
    if isinstance(entries, dict):
        pairs = list(entries.items())
    elif isinstance(entries, list):
        pairs = []
        for entry in entries:
            if isinstance(entry, dict):
                identifier = entry.get("id") or entry.get("model") or entry.get("name")
                if identifier:
                    pairs.append((identifier, entry))
    else:
        return {}
    windows: dict[str, int] = {}
    for identifier, entry in pairs:
        if not isinstance(entry, dict):
            continue
        for key in (
            "context_window",
            "contextWindow",
            "context_length",
            "max_context_tokens",
            "maxContextTokens",
        ):
            value = entry.get(key)
            if isinstance(value, int | float) and value > 0:
                windows[str(identifier)] = int(value)
                break
    return windows


def tokens_from_ratio(
    usage: dict[str, Any], catalogue: dict[str, Any] | None, *, model: str = ""
) -> dict[str, int] | None:
    """The tokens the provider's ratio implies, if the window is known.

    ``None`` means "not derivable" -- never a zero and never a guess.
    """
    if not isinstance(usage, dict) or not catalogue or not catalogue.get("known"):
        return None
    ratio = usage.get("context_usage_ratio")
    if not isinstance(ratio, int | float) or ratio <= 0:
        return None
    windows = catalogue.get("models") or {}
    window = (
        windows.get(model) or windows.get(f"qfmodel/{model}") or windows.get(model.split("/")[-1])
    )
    if not window:
        return None
    tokens = int(round(float(ratio) * int(window)))
    if tokens <= 0:
        return None
    return {"prompt_tokens": tokens, "completion_tokens": 0}


def credential_from_file(path: str) -> str:
    """A credential written in a file, if it is there."""
    if not path:
        return ""
    expanded = os.path.expanduser(path)
    try:
        return Path(expanded).read_text(encoding="utf-8").strip()
    except OSError:
        return ""
