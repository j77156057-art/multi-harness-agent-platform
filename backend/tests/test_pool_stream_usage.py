"""流式请求的用量必须被记账 —— 这是"计量档花了多少钱"的前提。

流式路径以前**一律记 0 token**：上游把 usage 放在最后一个事件里，而池子
只是把那些行原样转发出去。于是计量档花的钱在账上完全看不见。

这里用一个真的本地 HTTP 服务把这条路径走一遍：上游回一个带 usage 的
Responses 流，池子必须把它记下来。
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

from app.pool.service import PoolService
from app.pool.sources import Upstream

USAGE = {"input_tokens": 321, "output_tokens": 123, "total_tokens": 444}

BODY = (
    "event: response.output_text.delta\n"
    'data: {"type":"response.output_text.delta","delta":"hi"}\n\n'
    "event: response.completed\n"
    "data: " + json.dumps({"type": "response.completed", "response": {"usage": USAGE}}) + "\n\n"
)


class _Handler(BaseHTTPRequestHandler):
    def do_POST(self):  # noqa: N802 - the stdlib's own name
        length = int(self.headers.get("Content-Length") or 0)
        self.rfile.read(length)
        payload = BODY.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *args):  # keep the test output clean
        return


def _server() -> tuple[HTTPServer, str]:
    server = HTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, f"http://127.0.0.1:{server.server_port}"


def _service(tmp_path, base_url: str) -> PoolService:
    upstream = Upstream(
        name="test-chain",
        protocol="openai_responses",
        base_url=base_url,
        credential=("literal", "k"),
        models=("m-1",),
        tier="metered",
    )
    service = PoolService(home=tmp_path, usage_path=tmp_path / "usage.jsonl")
    # The discovery is shared and cached; the test serves one upstream of its own.
    service.upstreams = lambda refresh=False: [upstream]  # type: ignore[method-assign]
    return service


def test_a_streamed_request_is_accounted_with_its_tokens(tmp_path):
    server, base_url = _server()
    try:
        service = _service(tmp_path, base_url)
        chunks = list(
            service.responses_stream(
                {"model": "m-1", "input": "hi", "stream": True}, consumer="harness/x"
            )
        )
    finally:
        server.shutdown()

    assert any("response.completed" in chunk for chunk in chunks)
    rows = [
        json.loads(line)
        for line in (tmp_path / "usage.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert len(rows) == 1
    assert rows[0]["prompt_tokens"] == USAGE["input_tokens"]
    assert rows[0]["completion_tokens"] == USAGE["output_tokens"]
    assert rows[0]["consumer"] == "harness/x"
    assert rows[0]["upstream"] == "test-chain"


def test_the_accounted_stream_shows_up_in_the_spend(tmp_path):
    server, base_url = _server()
    try:
        service = _service(tmp_path, base_url)
        list(
            service.responses_stream(
                {"model": "m-1", "input": "hi", "stream": True}, consumer="harness/x"
            )
        )
        summary = service.usage_summary()
    finally:
        server.shutdown()

    assert summary["requests"] == 1
    assert summary["upstreams"]["test-chain"]["prompt_tokens"] == USAGE["input_tokens"]
    assert summary["upstreams"]["test-chain"]["completion_tokens"] == USAGE["output_tokens"]
    assert summary["consumers"]["harness/x"]["requests"] == 1
