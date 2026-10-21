"""计量档的"钱"：token 是平台自己的账，余额是 provider 自己报的数。

单价**算不出来**（这些模型名是中转站改过的，没有公开价格），所以平台只报
token 与余额，并且说清楚哪一条 provider 问不到。
"""

from __future__ import annotations

from app.pool.balance import parse_balance_answer, read_balance
from app.pool.service import _usage_blobs, _UsageSniffer
from app.pool.sources import Upstream


def _upstream(name: str, base_url: str, tier: str = "metered", key: str = "k"):
    return Upstream(
        name=name,
        protocol="openai_chat",
        base_url=base_url,
        credential=("literal", key),
        tier=tier,
    )


# --------------------------------------------------------------------------- #
# 流式用量：以前流式请求一律记 0 token
# --------------------------------------------------------------------------- #


def test_the_streamed_usage_is_found_where_the_protocol_puts_it():
    """Responses / chat / Anthropic 三种流，usage 放在不同的地方。

    流式路径以前**一律记 0 token** —— 而计量档花的钱就在这儿：不知道用了
    多少 token，就没法说花了多少钱。
    """
    sniffer = _UsageSniffer()
    sniffer.feed(
        'data: {"type":"response.completed","response":{"usage":'
        '{"input_tokens": 120, "output_tokens": 45}}}\n\n'
    )
    assert sniffer.prompt == 120
    assert sniffer.completion == 45

    chat = _UsageSniffer()
    chat.feed('data: {"choices":[],"usage":{"prompt_tokens": 7, "completion_tokens": 3}}\n\n')
    assert (chat.prompt, chat.completion) == (7, 3)

    anthropic = _UsageSniffer()
    anthropic.feed(
        'data: {"type":"message_start","message":{"usage":'
        '{"input_tokens": 900, "output_tokens": 0}}}\n\n'
    )
    anthropic.feed('data: {"type":"message_delta","usage":{"output_tokens": 60}}\n\n')
    assert anthropic.prompt == 900
    assert anthropic.completion == 60


def test_a_line_without_usage_leaves_the_counter_alone():
    sniffer = _UsageSniffer()
    sniffer.feed("data: [DONE]\n\n")
    sniffer.feed("event: ping\n")
    sniffer.feed("data: not json\n")
    sniffer.feed('data: {"type":"response.output_text.delta","delta":"hi"}\n\n')
    assert (sniffer.prompt, sniffer.completion) == (0, 0)


def test_the_watched_stream_passes_every_line_through():
    sniffer = _UsageSniffer()
    lines = [
        'data: {"type":"response.completed","response":{"usage":'
        '{"input_tokens": 5, "output_tokens": 2}}}\n\n',
        "data: [DONE]\n\n",
    ]
    assert list(sniffer.watched(iter(lines))) == lines
    assert sniffer.prompt == 5


def test_the_usage_blobs_are_collected_from_every_shape():
    assert _usage_blobs({"usage": {"input_tokens": 1}}) == [{"input_tokens": 1}]
    assert _usage_blobs({"response": {"usage": {"output_tokens": 2}}}) == [{"output_tokens": 2}]
    assert _usage_blobs({"message": {"usage": {"input_tokens": 3}}}) == [{"input_tokens": 3}]
    assert _usage_blobs({"nothing": 1}) == []


# --------------------------------------------------------------------------- #
# 余额：provider 自己报的数字
# --------------------------------------------------------------------------- #


def test_the_provider_balance_is_read_as_it_is():
    reading = parse_balance_answer(
        {
            "is_available": True,
            "balance_infos": [
                {
                    "currency": "CNY",
                    "total_balance": "1.04",
                    "granted_balance": "0.00",
                    "topped_up_balance": "1.04",
                }
            ],
        }
    )

    assert reading["known"] is True
    assert reading["balances"][0]["currency"] == "CNY"
    assert reading["balances"][0]["total"] == "1.04"


def test_a_provider_without_balances_says_so():
    reading = parse_balance_answer({"balance_infos": []})

    assert reading["known"] is False
    assert reading["reason"]


def test_a_provider_with_no_balance_endpoint_is_named_as_unknown():
    reading = read_balance(_upstream("codex:geekspace", "http://127.0.0.1:8402/v1"))

    assert reading["known"] is False
    assert reading["upstream"] == "codex:geekspace"
    assert "没有可查的余额接口" in reading["reason"]


def test_a_provider_without_a_credential_cannot_be_asked():
    upstream = Upstream(
        name="claude-provider",
        protocol="anthropic",
        base_url="https://api.deepseek.com/anthropic",
        credential=None,
        tier="metered",
    )

    reading = read_balance(upstream)

    assert reading["known"] is False
    assert "凭据" in reading["reason"]
