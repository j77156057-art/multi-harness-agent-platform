"""出站 HTTP 客户端的统一构造点。"""

from __future__ import annotations

from typing import Any

import httpx


def http_client(**kwargs: Any) -> httpx.Client:
    # 宿主代理环境变量可能带 httpx 解析不了的条目（如 NO_PROXY 里的 [::1]，
    # httpx 0.28.1 会抛 InvalidURL）。此时退回直连，而不是让整个功能崩掉。
    try:
        return httpx.Client(**kwargs)
    except httpx.InvalidURL:
        kwargs.pop("trust_env", None)
        return httpx.Client(trust_env=False, **kwargs)
