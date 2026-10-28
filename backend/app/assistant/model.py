"""The assistant's model channel: the pool, directly, free tier -- live.

Every ``step()`` is one OpenAI-compatible call to ``POST /pool/v1/chat/completions``;
the pool serves it from whichever upstream has the model -- including the CLI
slots (``_cli_chat``), which is why the *free* tier works with zero USD.  The
free tier VOLATILE: credits run out, models get retired, new ones appear.  So
the chain is NOT pinned:

* the live catalogue (``GET /pool/v1/models``, ``tier == "free"``) is pulled
  on a TTL -- the pool's discovery is the source of truth, the channel does
  not keep its own;
* a model that failed at serving sits in a cooldown (default half an hour)
  and is spoken to last (only when everything else is cold, it gets retried
  -- a poor service is better than none), so death is discovered by failure,
  never by polling (a probe to a CLI slot costs a real invocation);
* the pinned list below is only the *fallback* for the days the catalogue
  itself cannot be read -- and the trace then says so.

When nothing serves, the channel raises ``ChannelUnavailable`` -- it does not
quietly fall into the metered tier (that spend is a human's decision).

The upstream receipt (``usage``) is kept on the instance; the loop copies it
into the step trace.  Nothing here estimates tokens locally -- the pool's
metering is the ledger.
"""

from __future__ import annotations

import time
from typing import Any, Protocol


class ChannelUnavailable(RuntimeError):
    """No model in the chain could serve the step. The caller reports it."""


#: Fallback only: the free tier as the pool served it on 2026-10-23. The live
#: catalogue outranks this list whenever it can be read (see `_chain_now`).
FREE_MODELS: tuple[str, ...] = (
    "qoder/Qwen3.8-Flash",
    "qoder/DeepSeek-Flash",
    "codebuddy/hy3",
    "codebuddy/minimax-m3.1-flash",
    "opencode-cli/minimax-m3.1-flash-free",
)

#: The catalogue is the pool's business; a refreshed read occasionally is
#: honest bookkeeping, a read per step is chatty. Ten minutes is the middle.
CHAIN_TTL_SECONDS = 600.0
#: A model that just failed stays out of the front of the chain for this long.
COOLDOWN_SECONDS = 1800.0


class PoolClient(Protocol):
    """The worker's HTTP client (same duck type as admin's PlatformClient)."""

    def get(self, path: str, **params: Any) -> dict[str, Any]: ...

    def post(self, path: str, body: dict[str, Any]) -> dict[str, Any]: ...


def catalogue_free_models(client: PoolClient) -> tuple[str, ...]:
    """The pool's live free tier, or ``()`` when the catalogue cannot say.

    The pool already owns discovery and expiry; this only reads what it
    publishes.  A failed read yields an empty tuple -- the caller falls back
    to the pinned list and records that, honestly, in its own way.
    """
    try:
        data = client.get("/pool/v1/models")
    except Exception:  # noqa: BLE001 - a broken catalogue is a fallback, not a crash
        return ()
    entries = data.get("data")
    if not isinstance(entries, list):
        return ()
    names: list[str] = []
    seen: set[str] = set()
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        name = entry.get("id")
        if entry.get("tier") != "free" or not isinstance(name, str) or not name or name in seen:
            continue
        seen.add(name)
        names.append(name)
    return tuple(names)


class PoolModelChannel:
    """One model call per ReAct step; live chain, receipts kept, nothing guessed."""

    def __init__(
        self,
        client: PoolClient,
        *,
        fallback: tuple[str, ...] = FREE_MODELS,
        path: str = "/pool/v1/chat/completions",
        chain_ttl: float = CHAIN_TTL_SECONDS,
        cooldown: float = COOLDOWN_SECONDS,
        pin: Any = None,
    ) -> None:
        if not fallback:
            raise ValueError("fallback chain must not be empty")
        self.client = client
        self.fallback = tuple(fallback)
        self.path = path
        self.chain_ttl = chain_ttl
        self.cooldown = cooldown
        # ``pin`` 是"人给这个助手指定了哪个模型"（可调用对象，每次问它一下：
        # 人可能随时改）。有它就用它开头 —— 免费额度用完时，人从额度池里挑的
        # 那个模型必须真的被用上，否则那个选择是句空话。它失手了仍旧退回实况
        # 链：一个人挑的模型死了，不等于助手就该停摆。
        self.pin = pin
        now = time.monotonic()
        self._chain: tuple[str, ...] | None = None
        self._chain_at = now - self.chain_ttl  # resolve on first use
        self._chain_source = "unresolved"
        self._cooldowns: dict[str, float] = {}
        self.last_usage: dict[str, Any] | None = None
        self.last_model: str | None = None
        self.last_chain_source: str | None = None

    # ------------------------------------------------------------- chain

    def _pinned(self) -> str:
        if self.pin is None:
            return ""
        try:
            return str(self.pin() or "")
        except Exception:  # noqa: BLE001 - 问不出来就按没指定处理
            return ""

    def _chain_now(self) -> tuple[str, ...]:
        now = time.monotonic()
        if now - self._chain_at > self.chain_ttl:
            live = catalogue_free_models(self.client)
            if live:
                self._chain = live
                self._chain_source = "catalogue"
            else:
                self._chain = self.fallback
                self._chain_source = "pinned-fallback"
            self._chain_at = now
        assert self._chain is not None
        # 人挑的那个排最前：它是"这次用哪个"的答案，实况链只是兜底。
        chosen = self._pinned()
        if chosen and self._chain and self._chain[0] != chosen:
            rest = tuple(name for name in self._chain if name != chosen)
            return (chosen, *rest)
        if chosen and not self._chain:
            return (chosen,)
        return self._chain

    def _usable_chain(self) -> list[str]:
        now = time.monotonic()
        chain = [name for name in self._chain_now() if self._cooldowns.get(name, 0.0) <= now]
        if not chain:
            # Everything sits in a cooldown. Retrying the cold chain is the
            # last resort: a poor service beats none, and the cooldowns only
            # claim "failed recently", not "dead forever".
            chain = list(self._chain_now())
        return chain

    # ------------------------------------------------------------- serving

    def step(self, prompt: str) -> str:
        errors: list[str] = []
        self.last_usage = None
        self.last_model = None
        chain = self._usable_chain()  # resolves the chain on first use
        self.last_chain_source = self._chain_source
        for name in chain:
            try:
                data = self.client.post(
                    self.path,
                    {
                        "model": name,
                        "stream": False,
                        "messages": [{"role": "user", "content": prompt}],
                    },
                )
            except Exception as exc:  # noqa: BLE001 - one dead model is not the chain
                errors.append(f"{name}: {type(exc).__name__}: {exc}")
                self._cool(name)
                continue
            content = self._content(data)
            if content is None:
                errors.append(f"{name}: answer carried no message content")
                self._cool(name)
                continue
            usage = data.get("usage")
            self.last_usage = usage if isinstance(usage, dict) else {}
            self.last_model = name
            return content
        raise ChannelUnavailable(f"[chain={self._chain_source}] " + "; ".join(errors))

    def _cool(self, name: str) -> None:
        """A freshly failed model waits in the back of the chain for a while."""
        self._cooldowns[name] = time.monotonic() + self.cooldown

    @staticmethod
    def _content(data: dict[str, Any]) -> str | None:
        """The chat-completion text, or ``None`` when the shape is unusable."""
        choices = data.get("choices")
        if not isinstance(choices, list) or not choices:
            return None
        message = choices[0].get("message") if isinstance(choices[0], dict) else None
        if not isinstance(message, dict):
            return None
        content = message.get("content")
        if not isinstance(content, str) or not content.strip():
            return None
        return content


__all__ = [
    "FREE_MODELS",
    "CHAIN_TTL_SECONDS",
    "COOLDOWN_SECONDS",
    "ChannelUnavailable",
    "PoolModelChannel",
    "catalogue_free_models",
]
