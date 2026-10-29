"""The channel one platform-harness attempt talks to: the quota pool, in text.

Two facts shape this file.

**No tools in the request.**  The whole reason a platform harness exists is
that the routed channel cannot answer a ``tools`` request: a *CLI-as-model*
slot is another CLI's prose, and the pool now refuses ``tools`` for it
outright (with a message a human can read) instead of silently stripping them
and leaving the agent crippled.  So the request carries none -- the platform
loop is the one that "calls" tools, on this side of the wire.

**A transport failure is not a bad answer.**  Rate limits, a dead upstream and
an empty completion are the channel's fault; the attempt should go back to the
queue and run somewhere else, not be recorded as "the work was wrong".  The
messages raised here therefore use the same words the worker classifies
provider trouble by (``quota``, ``rate limit``, ``timed out``, ``connection
refused``) so that one vocabulary decides it everywhere.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass

import httpx


class ChannelFailed(RuntimeError):
    """The model channel could not answer; another channel might."""


@dataclass
class PoolChannel:
    """``POST {base_url}/chat/completions`` with this harness's own token."""

    base_url: str
    token: str
    model: str
    timeout: float = 120.0
    max_tokens: int = 3000
    retries: int = 2

    def text(self, prompt: str) -> str:
        return self.ask([{"role": "user", "content": prompt}])

    def ask(self, messages: list[dict[str, str]]) -> str:
        url = self.base_url.rstrip("/") + "/chat/completions"
        payload = {
            "model": self.model,
            "messages": messages,
            "max_tokens": self.max_tokens,
            "stream": False,
        }
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
        }
        last = ""
        for attempt in range(self.retries + 1):
            try:
                response = httpx.post(url, json=payload, headers=headers, timeout=self.timeout)
            except httpx.HTTPError as exc:
                last = f"connection refused / transport error: {type(exc).__name__}: {exc}"
                if attempt < self.retries:
                    time.sleep(2.0 * (attempt + 1))
                    continue
                raise ChannelFailed(f"模型通道连不上：{last}") from exc
            if response.status_code >= 400:
                body = response.text[:400].replace("\n", " ")
                last = f"HTTP {response.status_code}: {body}"
                # 4xx that is not a rate limit is this request's problem: retry
                # would send the same thing again.
                retryable = response.status_code in {408, 409, 425, 429} or response.status_code >= 500
                if attempt < self.retries and retryable:
                    time.sleep(2.0 * (attempt + 1))
                    continue
                raise ChannelFailed(
                    f"模型通道拒绝了这次请求（{last}）；"
                    "换一份额度或换一个模型还能继续"
                )
            try:
                document = response.json()
            except ValueError as exc:
                raise ChannelFailed(f"模型通道回来的不是 JSON：{response.text[:200]}") from exc
            content = _content_of(document)
            if content.strip():
                return content
            # An empty answer is a real thing this channel does (a CLI-as-model
            # that answered something else, or ran out of its own budget).
            last = "the completion was empty"
            if attempt < self.retries:
                time.sleep(1.5 * (attempt + 1))
                continue
            raise ChannelFailed(
                f"模型这次没有输出（{last}）；这次没有产出内容，等于通道不可用"
            )
        raise ChannelFailed(last or "模型通道失败")


def _content_of(document: object) -> str:
    if not isinstance(document, dict):
        return ""
    choices = document.get("choices")
    if not isinstance(choices, list) or not choices:
        return ""
    first = choices[0] if isinstance(choices[0], dict) else {}
    message = first.get("message")
    if isinstance(message, dict):
        content = message.get("content")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            # Anthropic-shaped blocks, in case a relay answers that way.
            pieces = [
                str(block.get("text") or "")
                for block in content
                if isinstance(block, dict) and block.get("type") in (None, "text")
            ]
            return "".join(pieces)
    text = first.get("text")
    return text if isinstance(text, str) else ""


def from_bundle(document: object) -> PoolChannel:
    """Build a channel from the per-attempt bundle the adapter wrote."""
    if not isinstance(document, dict):
        raise ChannelFailed("这次尝试没有拿到模型通道配置。")
    base = str(document.get("base_url") or "").strip()
    token = str(document.get("token") or "").strip()
    model = str(document.get("model") or "").strip()
    missing = [name for name, value in (("base_url", base), ("token", token), ("model", model)) if not value]
    if missing:
        raise ChannelFailed(
            "这次尝试没有可用的模型通道（缺 "
            + "、".join(missing)
            + "）。这个 Harness 的工具由平台执行，模型必须来自额度池："
            "请在额度池里给这个窗口指定一个模型。"
        )
    return PoolChannel(
        base_url=base,
        token=token,
        model=model,
        timeout=float(document.get("timeout") or 120.0),
        max_tokens=int(document.get("max_tokens") or 3000),
    )


def dumps(document: object) -> str:
    return json.dumps(document, ensure_ascii=False, sort_keys=True)
