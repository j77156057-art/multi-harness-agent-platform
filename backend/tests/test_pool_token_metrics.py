"""token 度量：窗口页要问的四个数 —— 缓存命中、每秒生成、每小时消耗。

以前平台**不记缓存 token**（各家协议把它放在不同地方），所以「缓存命中率」
根本算不出来；汇总层也只给请求数与模型名，**不给 token**。

这里用一个真的本地 HTTP 服务走一遍流式路径：上游报带缓存明细的 usage，池子必须
把它记下来，并且能从账上算出「每秒生成多少 token」「每小时多少」「命中多少」。
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

from app.pool.service import PoolService
from app.pool.sources import Upstream

USAGE = {
    "input_tokens": 400,
    "output_tokens": 200,
    "total_tokens": 600,
    "input_tokens_details": {"cached_tokens": 160},
}

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
    service.upstreams = lambda refresh=False: [upstream]  # type: ignore[method-assign]
    return service


def _account_one(tmp_path) -> PoolService:
    server, base_url = _server()
    try:
        service = _service(tmp_path, base_url)
        list(
            service.responses_stream(
                {"model": "m-1", "input": "hi", "stream": True}, consumer="harness/x"
            )
        )
    finally:
        server.shutdown()
    return service


def test_the_cached_tokens_are_accounted(tmp_path):
    """provider 报的缓存命中要进账 —— 否则命中率永远是「没数到」。"""
    _account_one(tmp_path)
    rows = [
        json.loads(line)
        for line in (tmp_path / "usage.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert len(rows) == 1
    assert rows[0]["cached_tokens"] == USAGE["input_tokens_details"]["cached_tokens"]


def test_the_window_gets_its_tokens_and_the_rate(tmp_path):
    """按窗口（consumer）给 token，并算出每秒生成多少。"""
    service = _account_one(tmp_path)
    summary = service.usage_summary()
    spent = summary["consumers"]["harness/x"]
    assert spent["prompt_tokens"] == USAGE["input_tokens"]
    assert spent["completion_tokens"] == USAGE["output_tokens"]
    assert spent["cached_tokens"] == USAGE["input_tokens_details"]["cached_tokens"]
    # 缓存命中率 = 命中的 ÷ 输入
    assert spent["cache_hit_rate"] == round(
        USAGE["input_tokens_details"]["cached_tokens"] / USAGE["input_tokens"], 4
    )
    # 每秒生成 = 生成 token ÷ 生成耗时（本地服务很快，数值大；只断言它是正数）
    assert isinstance(spent["generation_rate"], int | float)
    assert spent["generation_rate"] > 0


def test_the_hour_buckets_carry_each_window_s_tokens(tmp_path):
    """每小时消耗：时间线的小时桶里要有这个窗口的 token。"""
    service = _account_one(tmp_path)
    timeline = service.usage_timeline()
    spent = [
        bucket["consumer_tokens"]["harness/x"]
        for bucket in timeline["buckets"]
        if "harness/x" in (bucket.get("consumer_tokens") or {})
    ]
    assert spent, "这一小时里应该有这个窗口的 token"
    assert spent[0]["prompt_tokens"] == USAGE["input_tokens"]
    assert spent[0]["completion_tokens"] == USAGE["output_tokens"]


def test_a_record_without_usage_is_not_a_zero(tmp_path):
    """没数到就是没数到：不给 0（0 会被读成「这次是免费的」）。"""
    service = PoolService(home=tmp_path, usage_path=tmp_path / "usage.jsonl")
    service.usage_path.write_text(
        json.dumps(
            {
                "at": "2026-10-24T10:00:00+00:00",
                "model": "m-1",
                "upstream": "u",
                "protocol": "openai_responses",
                "status": 200,
                "duration_ms": 10,
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "usage_reported": False,
                "consumer": "harness/x",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    summary = service.usage_summary()
    spent = summary["consumers"]["harness/x"]
    assert spent["prompt_tokens"] == 0
    # 分母是 0 → 命中率不给数
    assert spent["cache_hit_rate"] is None
