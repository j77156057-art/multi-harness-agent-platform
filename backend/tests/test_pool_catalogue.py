"""把 provider 的**比例**换成 token\uff1a\u7b97\u6cd5\u662f provider \u81ea\u5df1\u7684\u6570\u5b57\uff0c\u4f9d\u636e\u662f provider \u81ea\u5df1\u7684\u6a21\u578b\u76ee\u5f55\u3002

\u6ca1\u6709\u76ee\u5f55\uff08\u6216\u6ca1\u6709\u51ed\u636e\uff09\u5c31\u6ca1\u6709\u7a97\u53e3\u5bbd\u5ea6\uff0c\u90a3\u5c31\u4e0d\u6362\u7b97\u2014\u2014\u7528\u91cf\u7559\u5728 provider \u81ea\u5df1\u7684
\u5355\u4f4d\uff08credits\uff09\u91cc\uff0c**\u4e0d\u7f16\u6570\u5b57**\u3002
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

from app.pool.catalogue import parse_windows, read_catalogue, tokens_from_ratio

CATALOGUE = {
    "data": [
        {"id": "Qwen3.8-Flash", "context_window": 131072},
        {"id": "DeepSeek-Flash", "context_window": 65536},
    ]
}


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802 - the stdlib's own name
        if self.headers.get("Authorization") != "Bearer k":
            self.send_response(401)
            self.end_headers()
            return
        payload = json.dumps(CATALOGUE).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *args):
        return


def _server() -> tuple[HTTPServer, str]:
    server = HTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_port}"


def test_the_catalogue_is_read_in_either_shape():
    listed = parse_windows({"data": [{"id": "m", "context_window": 4096}]})
    mapped = parse_windows({"models": {"m": {"contextWindow": 4096}}})

    assert listed == {"m": 4096}
    assert mapped == {"m": 4096}


def test_a_catalogue_without_a_window_is_not_a_window():
    assert parse_windows({"data": [{"id": "m", "context_window": 0}]}) == {}
    assert parse_windows({"data": [{"id": "m"}]}) == {}


def test_the_tokens_are_the_ratio_times_the_providers_own_window():
    catalogue = {"known": True, "models": {"Qwen3.8-Flash": 131072}}
    usage = {"context_usage_ratio": 0.11206111111111111, "credits": 0.5616}

    tokens = tokens_from_ratio(usage, catalogue, model="Qwen3.8-Flash")

    assert tokens is not None
    # The provider's own numbers: no price was invented.
    assert tokens["prompt_tokens"] == int(round(0.11206111111111111 * 131072))


def test_without_a_window_the_tokens_are_not_derived():
    assert tokens_from_ratio({"context_usage_ratio": 0.5}, None, model="m") is None
    assert (
        tokens_from_ratio(
            {"context_usage_ratio": 0.5},
            {"known": True, "models": {"other": 4096}},
            model="m",
        )
        is None
    )
    assert (
        tokens_from_ratio(
            {"context_usage_ratio": 0.5},
            {"known": True, "models": {"m": 4096}},
            model="m",
        )
        is not None
    )


def test_the_catalogue_is_read_read_only_and_says_why_it_cannot():
    server, base = _server()
    try:
        good = read_catalogue(base_url=base, path="/models", credential="k")
        refused = read_catalogue(base_url=base, path="/models", credential="wrong")
    finally:
        server.shutdown()

    assert good["known"] is True
    assert good["models"]["Qwen3.8-Flash"] == 131072
    assert refused["known"] is False
    assert refused["reason"]


def test_without_a_credential_the_catalogue_is_not_read():
    reading = read_catalogue(base_url="http://127.0.0.1:1", path="/models", credential="")

    assert reading["known"] is False
    assert "凭据" in reading["reason"]


def test_a_slot_with_a_catalogue_derives_the_tokens_from_the_ratio(tmp_path):
    """The whole path: the provider's ratio + the provider's window."""
    from app.pool.cli_model import CliModelSource

    server, base = _server()
    try:
        token_file = tmp_path / "api-token"
        token_file.write_text("k", encoding="utf-8")
        source = CliModelSource(
            name="qoder",
            model="Qwen3.8-Flash",
            binary="qoderclicn",
            catalogue={
                "base_url": base,
                "path": "/models",
                "credential_file": str(token_file),
            },
        )
        report = {"usage": None, "raw_usage": {"context_usage_ratio": 0.5, "credits": 1.0}}
        source._derive_from_catalogue(report)
    finally:
        server.shutdown()

    assert report["usage"] is not None
    assert report["usage"]["prompt_tokens"] == 0.5 * 131072
    assert "ratio" in report["usage_source"]


def test_a_slot_without_a_credential_keeps_the_usage_in_credits(tmp_path):
    from app.pool.cli_model import CliModelSource

    source = CliModelSource(
        name="qoder",
        model="Qwen3.8-Flash",
        binary="qoderclicn",
        catalogue={
            "base_url": "https://gateway.qoder.com.cn",
            "path": "/api/v1/models",
            "credential_file": str(tmp_path / "not-there"),
        },
    )
    report = {"usage": None, "raw_usage": {"context_usage_ratio": 0.5, "credits": 1.0}}

    source._derive_from_catalogue(report)

    assert report["usage"] is None
    assert report["catalogue_note"]
