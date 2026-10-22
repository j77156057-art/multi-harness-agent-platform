"""计量档的"钱"：token 是平台自己的账，余额是 provider 自己报的数。

单价**算不出来**（这些模型名是中转站改过的，没有公开价格），所以平台只报
token 与余额，并且说清楚哪一条 provider 问不到。
"""

from __future__ import annotations

import json

from app.pool.balance import parse_balance_answer, parse_usage_answer, read_balance
from app.pool.service import PoolService, _usage_blobs, _UsageSniffer
from app.pool.sources import Upstream
from scripts.backfill_usage_corrections import corrections_for


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


def test_the_usage_answer_keeps_the_wallet_and_the_daily_rows():
    reading = parse_usage_answer(
        {
            "unit": "USD",
            "balance": 0.17,
            "remaining": 0.17,
            "daily_usage": [
                {
                    "date": "2026-10-21",
                    "requests": 28,
                    "input_tokens": 264807,
                    "output_tokens": 13980,
                    "total_tokens": 819779,
                    "cost": 0.9940092,
                    "actual_cost": 0.0477124416,
                }
            ],
        }
    )

    assert reading["known"] is True
    assert reading["remaining"] == 0.17
    assert reading["unit"] == "USD"
    assert reading["daily"][0]["actual_cost"] == 0.0477124416
    # The daily rows are the **account's**, not the platform's: the platform is
    # one of its callers, and saying otherwise would misreport someone's bill.
    assert "账号" in reading["scope"]


def test_a_provider_with_no_balance_endpoint_is_named_as_unknown():
    reading = read_balance(_upstream("opencode:1", "https://relay.example.com/v1"))

    assert reading["known"] is False
    assert reading["upstream"] == "opencode:1"
    assert "没有可查的余额接口" in reading["reason"]


def test_the_codex_chain_is_asked_its_own_usage_endpoint():
    """codex 链的 provider 回答 ``/v1/usage``（本地代理透传）。

    代理自己注入真密钥，池子这一跳不需要自己的凭据。
    """
    from app.pool.balance import _NAME_READERS

    upstream = _upstream("codex:geekspace", "http://127.0.0.1:8402")
    reader = next(
        (fn for prefix, fn in _NAME_READERS.items() if upstream.name.startswith(prefix)), None
    )

    assert reader is not None
    assert callable(reader)


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


def test_a_cli_that_answers_with_one_json_document_is_read_too():
    """mmx 形状：整个输出是**一份** JSON 文档（不是一行一个事件）。

    它的 ``usage`` 就在文档里——但槽里那个 ``--quiet`` 会把 JSON（连同 usage）
    一起扔掉，只剩答案，所以槽不能带它。
    """
    from app.pool.cli_model import extract_report

    report = extract_report(
        json.dumps(
            {
                "id": "msg-1",
                "type": "message",
                "role": "assistant",
                "model": "MiniMax-M3.1-Flash-Preview",
                "content": [{"text": "OK", "type": "text"}],
                "usage": {
                    "input_tokens": 42,
                    "output_tokens": 12,
                    "cache_read_input_tokens": 7,
                },
            }
        )
    )

    assert report["text"] == "OK"
    assert report["usage"] == {"prompt_tokens": 42, "completion_tokens": 12}


def test_the_mmx_slot_does_not_quiet_away_its_usage():
    """``--quiet`` 只留答案：JSON（和 usage）就没了。"""
    from app.pool.sources import DEFAULT_CLI_SLOTS

    mmx = next(slot for slot in DEFAULT_CLI_SLOTS if slot["name"] == "mmx")

    assert "--output" in mmx["args"] and "json" in mmx["args"]
    assert "--quiet" not in mmx["args"]


def test_a_cli_that_reports_the_shape_but_no_numbers_has_no_usage():
    """qoder CLI：usage 的**形状**在，数字全是 0（它的 provider 记 credits，不记 token）。

    全 0 的 usage 不是"这次没用 token"，是"它没告诉我们"——按未记上处理。
    """
    from app.pool.cli_model import extract_report

    report = extract_report(
        "\n".join(
            [
                json.dumps(
                    {
                        "type": "result",
                        "subtype": "success",
                        "result": "OK",
                        "usage": {
                            "input_tokens": 0,
                            "output_tokens": 0,
                            "cache_read_input_tokens": 0,
                            "credits": 0.56,
                        },
                    }
                )
            ]
        )
    )

    assert report["text"] == "OK"
    assert report["usage"] is None


# --------------------------------------------------------------------------- #
# CLI 槽：用量是 CLI 自己报的
# --------------------------------------------------------------------------- #


def test_the_cli_usage_is_taken_from_the_clis_own_output():
    """Claude-Code 形状的流（codebuddy / qoder）在最后一行报整轮的 usage。

    那是 **CLI 自己的数**——池子不推算，只照抄。
    """
    from app.pool.cli_model import extract_report

    report = extract_report(
        "\n".join(
            [
                json.dumps({"type": "system", "subtype": "init", "session_id": "s-1"}),
                json.dumps(
                    {
                        "type": "assistant",
                        "session_id": "s-1",
                        "message": {"content": [{"type": "text", "text": "OK"}]},
                    }
                ),
                json.dumps(
                    {
                        "type": "result",
                        "subtype": "success",
                        "session_id": "s-1",
                        "result": "OK",
                        "usage": {
                            "input_tokens": 23513,
                            "output_tokens": 24,
                            "cache_read_input_tokens": 512,
                        },
                    }
                ),
            ]
        )
    )

    assert report["text"] == "OK"
    assert report["session_id"] == "s-1"
    assert report["usage"] == {"prompt_tokens": 23513, "completion_tokens": 24}


def test_a_cli_that_reports_no_usage_has_none():
    """opencode 形状的流只报答案与会话 id——没有 usage 就是没有。

    平台不能因为"应该有"就填一个 0：那会把"没数过"说成"没用过"。
    """
    from app.pool.cli_model import extract_report

    report = extract_report(
        "\n".join(
            [
                json.dumps({"type": "step_start", "sessionID": "s-2", "part": {}}),
                json.dumps(
                    {"type": "text", "sessionID": "s-2", "part": {"text": "OK"}, "tokens": {}}
                ),
            ]
        )
    )

    assert report["text"] == "OK"
    assert report["session_id"] == "s-2"
    assert report["usage"] is None


def test_a_record_whose_cli_reported_no_usage_is_not_a_zero(tmp_path):
    usage = tmp_path / "usage.jsonl"
    usage.write_text(
        json.dumps(
            {
                "at": "2026-10-22T01:00:00.000000+00:00",
                "model": "opencode/minimax-m3.1-flash-free",
                "upstream": "opencode-cli",
                "protocol": "cli",
                "status": 200,
                "duration_ms": 100,
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "usage_reported": False,
                "session_id": "ses_abc",
                "consumer": "harness/opencode",
                "note": "served by the CLI slot",
            }
        )
        + "\n",
        encoding="utf-8",
    )

    summary = PoolService(home=tmp_path, usage_path=usage).usage_summary()

    assert summary["requests"] == 1
    assert summary["tokens_unknown"] == 1
    assert summary["upstreams"]["opencode-cli"]["tokens_unknown"] == 1


# --------------------------------------------------------------------------- #
# 补历史：没数过的 token 不能写成 0
# --------------------------------------------------------------------------- #


def test_a_record_whose_usage_was_never_counted_is_not_a_zero(tmp_path):
    """池子还不看流式 usage 时记下的 0，不是"这次没用 token"，是"没数过"。

    补历史不能编一个数字出来；能做的只是把"没数过"如实标出来。
    """
    usage = tmp_path / "usage.jsonl"
    usage.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "at": "2026-10-21T02:22:20.071535+00:00",
                        "model": "gpt-6.1-sol",
                        "upstream": "codex:geekspace",
                        "protocol": "openai_responses",
                        "status": 200,
                        "duration_ms": 100,
                        "prompt_tokens": 0,
                        "completion_tokens": 0,
                        "consumer": "harness/opencode",
                        "note": "streamed (responses)",
                    }
                ),
                json.dumps(
                    {
                        "at": "2026-10-21T02:30:00.000000+00:00",
                        "model": "MiniMax-M3.1-Flash-Preview",
                        "upstream": "minimax",
                        "protocol": "anthropic",
                        "status": 200,
                        "duration_ms": 100,
                        "prompt_tokens": 98,
                        "completion_tokens": 15,
                        "consumer": "harness/opencode",
                        "note": "",
                    }
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    service = PoolService(home=tmp_path, usage_path=usage)
    before = service.usage_summary()
    assert before["requests"] == 2
    assert before["tokens_unknown"] == 0

    corrections = corrections_for(usage)
    assert len(corrections) == 1
    assert corrections[0]["upstream"] == "codex:geekspace"
    (tmp_path / "usage-corrections.jsonl").write_text(
        "\n".join(json.dumps(correction, ensure_ascii=False) for correction in corrections) + "\n",
        encoding="utf-8",
    )

    after = service.usage_summary()
    assert after["requests"] == 2
    assert after["tokens_unknown"] == 1
    assert after["upstreams"]["codex:geekspace"]["tokens_unknown"] == 1
    # 真的数过的那条不受影响。
    assert after["upstreams"]["minimax"]["prompt_tokens"] == 98
    assert after["upstreams"]["minimax"]["tokens_unknown"] == 0


def test_the_corrections_skip_the_records_that_were_counted(tmp_path):
    usage = tmp_path / "usage.jsonl"
    usage.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "at": "2026-10-21T02:30:00.000000+00:00",
                        "model": "m",
                        "upstream": "minimax",
                        "prompt_tokens": 98,
                        "completion_tokens": 15,
                        "consumer": "harness/x",
                        "note": "",
                    }
                ),
                json.dumps(
                    {
                        "at": "2026-10-21T02:31:00.000000+00:00",
                        "model": "m",
                        "upstream": "codebuddy:hy3",
                        "prompt_tokens": 0,
                        "completion_tokens": 0,
                        "consumer": "harness/x",
                        "note": "served by the CLI slot",
                    }
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    corrections = corrections_for(usage)

    assert [item["upstream"] for item in corrections] == ["codebuddy:hy3"]
