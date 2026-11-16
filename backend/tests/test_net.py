"""出站客户端构造点：宿主代理变量坏了也要能建出客户端。"""

from __future__ import annotations

import httpx

from app.net import http_client


def test_client_builds_with_clean_env(monkeypatch):
    monkeypatch.delenv("NO_PROXY", raising=False)
    with http_client(timeout=1.0) as client:
        assert isinstance(client, httpx.Client)


def test_unparsable_no_proxy_falls_back_to_direct(monkeypatch):
    # httpx 0.28.1 解析不了 NO_PROXY 里的 [::1]；构造必须退回直连而不是抛错。
    monkeypatch.setenv("NO_PROXY", "localhost,127.0.0.1,[::1],::1")
    with http_client(timeout=1.0) as client:
        assert isinstance(client, httpx.Client)


def test_unparsable_proxy_falls_back_to_direct(monkeypatch):
    monkeypatch.setenv("HTTPS_PROXY", "http://bad host:port")
    with http_client(timeout=1.0) as client:
        assert isinstance(client, httpx.Client)
