"""The assistant's model channel: the pool, directly, free tier first.

Every ``step()`` is one OpenAI-compatible call to ``POST /pool/v1/chat/completions``;
the pool serves it from whichever upstream has the model -- including the CLI
slots (``_cli_chat``), which is why the *free* harness slot list below works
with zero USD.  The chain prefers the free tier in a fixed order and falls
back down it; when nothing serves, the channel raises ``ChannelUnavailable``
-- it does not quietly fall into the metered tier (that spend is a human's
decision, not the assistant's).

The upstream receipt (``usage``) is kept on the instance; the loop copies it
into the step trace.  Nothing here estimates tokens locally -- the pool's
metering is the ledger.
"""

from __future__ import annotations

from typing import Any, Protocol


class ChannelUnavailable(RuntimeError):
    """No model in the chain could serve the step. The caller reports it."""


#: The free tier, as served by the pool at the time of writing (2026-10-23
#: pool catalogue read). CLI slots, each consumed through the pool's own
#: declared CLI machinery -- the pool meters them and gets a receipt.
FREE_MODELS: tuple[str, ...] = (
    "qoder/Qwen3.8-Flash",
    "qoder/DeepSeek-Flash",
    "codebuddy/hy3",
    "codebuddy/minimax-m3.1-flash",
    "opencode-cli/minimax-m3.1-flash-free",
)


class PoolClient(Protocol):
    """The worker's HTTP client (same duck type as admin's PlatformClient)."""

    def post(self, path: str, body: dict[str, Any]) -> dict[str, Any]: ...


class PoolModelChannel:
    """One model call per ReAct step; receipts kept, nothing guessed."""

    def __init__(
        self,
        client: PoolClient,
        *,
        models: tuple[str, ...] = FREE_MODELS,
        path: str = "/pool/v1/chat/completions",
    ) -> None:
        if not models:
            raise ValueError("models chain must not be empty")
        self.client = client
        self.models = tuple(models)
        self.path = path
        self.last_usage: dict[str, Any] | None = None
        self.last_model: str | None = None

    def step(self, prompt: str) -> str:
        errors: list[str] = []
        self.last_usage = None
        self.last_model = None
        for name in self.models:
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
                continue
            content = self._content(data)
            if content is None:
                errors.append(f"{name}: answer carried no message content")
                continue
            usage = data.get("usage")
            self.last_usage = usage if isinstance(usage, dict) else {}
            self.last_model = name
            return content
        raise ChannelUnavailable("; ".join(errors) if errors else "empty chain")

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


__all__ = ["FREE_MODELS", "ChannelUnavailable", "PoolModelChannel"]
