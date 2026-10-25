"""The user's **own** provider keys, and the platform's asking to spend them.

The pool's rule so far has been: *the platform owns no model credentials* — it
discovers the upstreams the harness CLIs already talk to and takes each key
from the place it already lives ("the relay is a switchboard, not a key
vault").  A key the **user** typed into the office is different: nobody else
has it, and nobody else can reach it, so the platform has to keep it.

That is why this is a separate store and a separate tier:

* **storage** — ``data/user-keys/`` (one file per key, plus an index).  File
  based on purpose: a credential is runtime state, not business state, so it
  does not belong in the schema (and a key in the schema would be a secret
  in a place meant to be diffed).
* **tier** — ``user``.  It sits *after* ``free`` and ``metered`` and, like
  ``reserve``, the platform does **not** spend it on its own: the user has to
  say yes.  ``reserve`` is "the plan kept in reserve"; a user key is "your own
  money", and asking is the whole point.
* **the asking** — when the free and metered tiers cannot serve a request and
  a user key exists but was not approved, the platform records a *request*
  (:func:`raise_request`).  The office shows it ("免费额度用完了,可以用你的
  key 吗?"); the user approves once or for good, and only then does the pool
  spend it.

Two rules this module keeps, whatever the caller does:

* **the key is never returned.**  Only a mask (``sk-…abcd``) leaves it.
* **the key is never logged.**  It goes to a file and is read at call time.
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import secrets
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.config import settings

#: Where the user's own keys live.  Under the data dir, which is gitignored.
KEY_DIR_NAME = "user-keys"
INDEX_NAME = "index.json"

#: The tier a user key sits in.  Same idea as ``reserve``: spending it has to
#: be explicit, so the platform never picks it on its own.
USER_TIER = "user"

#: The approval modes.  ``once`` is spent on the next request and falls back
#: to ``none``; ``always`` stands until the user removes the key.
APPROVAL_NONE = "none"
APPROVAL_ONCE = "once"
APPROVAL_ALWAYS = "always"

#: The protocols a key can speak.  Same three the relay already speaks.
PROTOCOLS = ("openai_chat", "openai_responses", "anthropic")

_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,39}$")
_LABEL_RE = re.compile(r"^[^\n\t]{1,60}$")


class UserKeyError(ValueError):
    """The user asked for something the store cannot do."""


@dataclass(slots=True)
class UserKey:
    """One key the user gave the platform, and what they allowed."""

    id: str
    label: str
    protocol: str
    base_url: str
    models: tuple[str, ...] = ()
    approval: str = APPROVAL_NONE
    created_at: str = ""
    used_at: str | None = None
    requests: int = 0
    note: str = ""

    def as_dict(self) -> dict[str, Any]:
        """What the office may see.  **No key, ever.**"""
        return {
            "id": self.id,
            "label": self.label,
            "protocol": self.protocol,
            "base_url": self.base_url,
            "models": list(self.models),
            "approval": self.approval,
            "created_at": self.created_at,
            "used_at": self.used_at,
            "requests": self.requests,
            "note": self.note,
        }


@dataclass(slots=True)
class PendingRequest:
    """The platform asked the user to spend a key; the user has not answered."""

    id: str
    key_id: str
    label: str
    at: str
    reason: str = ""
    task_id: str = ""
    model: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "key_id": self.key_id,
            "label": self.label,
            "at": self.at,
            "reason": self.reason,
            "task_id": self.task_id,
            "model": self.model,
        }


@dataclass(slots=True)
class _Store:
    """The on-disk shape.  Kept separate so the file format can change alone."""

    keys: list[dict[str, Any]] = field(default_factory=list)
    requests: list[dict[str, Any]] = field(default_factory=list)


def key_dir() -> Path:
    return Path(settings.data_dir) / KEY_DIR_NAME


def index_path() -> Path:
    return key_dir() / INDEX_NAME


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime())


def _read() -> _Store:
    path = index_path()
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return _Store()
    if not isinstance(document, dict):
        return _Store()
    keys = document.get("keys")
    requests = document.get("requests")
    return _Store(
        keys=[row for row in (keys or []) if isinstance(row, dict)],
        requests=[row for row in (requests or []) if isinstance(row, dict)],
    )


def _write(store: _Store) -> None:
    path = index_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(
        json.dumps({"keys": store.keys, "requests": store.requests}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    os.replace(temporary, path)


def _secret_path(key_id: str) -> Path:
    return key_dir() / f"{key_id}.key"


def _mask(value: str) -> str:
    """What the office may see of a key: the tail only."""
    text = str(value or "").strip()
    if not text:
        return ""
    if len(text) <= 8:
        return "…" + text[-4:]
    return f"{text[:3]}…{text[-4:]}"


def masked(key_id: str) -> str:
    """The mask of a stored key, read from the file (the key never comes back)."""
    try:
        return _mask(_secret_path(key_id).read_text(encoding="utf-8"))
    except OSError:
        return ""


def entries() -> list[UserKey]:
    """Every key the user has given the platform."""
    result: list[UserKey] = []
    for row in _read().keys:
        try:
            result.append(
                UserKey(
                    id=str(row.get("id") or ""),
                    label=str(row.get("label") or ""),
                    protocol=str(row.get("protocol") or "openai_chat"),
                    base_url=str(row.get("base_url") or ""),
                    models=tuple(str(m) for m in (row.get("models") or [])),
                    approval=str(row.get("approval") or APPROVAL_NONE),
                    created_at=str(row.get("created_at") or ""),
                    used_at=row.get("used_at"),
                    requests=int(row.get("requests") or 0),
                    note=str(row.get("note") or ""),
                )
            )
        except (TypeError, ValueError):
            continue
    return [key for key in result if key.id]


def find(key_id: str) -> UserKey | None:
    for key in entries():
        if key.id == key_id:
            return key
    return None


def secret(key_id: str) -> str | None:
    """The key itself.  Only the pool's call path may use this."""
    try:
        value = _secret_path(key_id).read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return value or None


def register(
    *,
    label: str,
    protocol: str,
    base_url: str,
    key: str,
    models: list[str] | tuple[str, ...] = (),
    note: str = "",
) -> UserKey:
    """Add one of the user's own keys.

    The key is written to its own file under ``data/user-keys/`` and never
    leaves the machine in clear text.
    """
    clean_label = str(label or "").strip()
    if not _LABEL_RE.match(clean_label):
        raise UserKeyError("名字要短一点（1 到 60 个字，不能换行）")
    clean_protocol = str(protocol or "openai_chat").strip()
    if clean_protocol not in PROTOCOLS:
        raise UserKeyError(f"不认识的协议 {clean_protocol!r}")
    clean_url = str(base_url or "").strip().rstrip("/")
    if not clean_url.startswith(("http://", "https://")):
        raise UserKeyError("地址要写成 http:// 或 https:// 开头的")
    clean_key = str(key or "").strip()
    if not clean_key:
        raise UserKeyError("key 不能是空的")
    if any(secret_text in clean_key for secret_text in ("\n", "\r")):
        raise UserKeyError("key 里不能有换行")

    store = _read()
    key_id = f"k{secrets.token_hex(3)}"
    while any(row.get("id") == key_id for row in store.keys):
        key_id = f"k{secrets.token_hex(3)}"
    _secret_path(key_id).parent.mkdir(parents=True, exist_ok=True)
    _secret_path(key_id).write_text(clean_key, encoding="utf-8")
    store.keys.append(
        {
            "id": key_id,
            "label": clean_label,
            "protocol": clean_protocol,
            "base_url": clean_url,
            "models": [str(m).strip() for m in models if str(m).strip()],
            "approval": APPROVAL_NONE,
            "created_at": _now(),
            "used_at": None,
            "requests": 0,
            "note": str(note or "").strip(),
        }
    )
    _write(store)
    found = find(key_id)
    if found is None:  # pragma: no cover - the row was just written
        raise UserKeyError("这个 key 没能存下来")
    return found


def remove(key_id: str) -> bool:
    """Forget one key: the file goes, the row goes."""
    store = _read()
    before = len(store.keys)
    store.keys = [row for row in store.keys if row.get("id") != key_id]
    store.requests = [row for row in store.requests if row.get("key_id") != key_id]
    _write(store)
    with contextlib.suppress(OSError):
        _secret_path(key_id).unlink()
    return len(store.keys) < before


def set_approval(key_id: str, approval: str) -> UserKey:
    """Say how much this key may be spent: not at all / once / always."""
    mode = str(approval or APPROVAL_NONE).strip()
    if mode not in (APPROVAL_NONE, APPROVAL_ONCE, APPROVAL_ALWAYS):
        raise UserKeyError(f"不认识的授权方式 {mode!r}")
    store = _read()
    touched = False
    for row in store.keys:
        if row.get("id") != key_id:
            continue
        row["approval"] = mode
        touched = True
    if not touched:
        raise UserKeyError("没有这个 key")
    _write(store)
    found = find(key_id)
    if found is None:  # pragma: no cover - the row is there
        raise UserKeyError("没有这个 key")
    return found


def approved(key_id: str) -> bool:
    """May this key be spent right now?"""
    key = find(key_id)
    return key is not None and key.approval in (APPROVAL_ONCE, APPROVAL_ALWAYS)


def usable() -> list[UserKey]:
    """The keys the platform may spend **right now**."""
    return [key for key in entries() if key.approval in (APPROVAL_ONCE, APPROVAL_ALWAYS)]


def spend_once(key_id: str) -> None:
    """An ``once`` approval is spent by the request that used it."""
    store = _read()
    for row in store.keys:
        if row.get("id") != key_id:
            continue
        row["used_at"] = _now()
        row["requests"] = int(row.get("requests") or 0) + 1
        if row.get("approval") == APPROVAL_ONCE:
            row["approval"] = APPROVAL_NONE
    _write(store)


def raise_request(
    *, key_id: str, reason: str, task_id: str = "", model: str = ""
) -> PendingRequest:
    """Ask the user to spend one of their keys.

    Recorded so the office can show the question.  Repeating the same question
    does not pile up: one open request per key.
    """
    store = _read()
    key = next((row for row in store.keys if row.get("id") == key_id), None)
    label = str((key or {}).get("label") or key_id)
    request = PendingRequest(
        id=f"r{secrets.token_hex(3)}",
        key_id=key_id,
        label=label,
        at=_now(),
        reason=str(reason or ""),
        task_id=str(task_id or ""),
        model=str(model or ""),
    )
    store.requests = [row for row in store.requests if row.get("key_id") != key_id]
    store.requests.append(request.as_dict())
    _write(store)
    return request


def pending() -> PendingRequest | None:
    """The open question, if there is one."""
    rows = _read().requests
    if not rows:
        return None
    row = rows[-1]
    return PendingRequest(
        id=str(row.get("id") or ""),
        key_id=str(row.get("key_id") or ""),
        label=str(row.get("label") or ""),
        at=str(row.get("at") or ""),
        reason=str(row.get("reason") or ""),
        task_id=str(row.get("task_id") or ""),
        model=str(row.get("model") or ""),
    )


def clear_request(key_id: str) -> None:
    """The user answered: the question is closed."""
    store = _read()
    store.requests = [row for row in store.requests if row.get("key_id") != key_id]
    _write(store)
