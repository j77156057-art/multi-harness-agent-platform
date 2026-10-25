"""OFFICE-UI-26：成员自报的用量与缓存命中率。

两笔账各管各的：

* **额度池记的** —— 走额度池的调用，平台自己记的；
* **成员自报的** —— 成员用自带模型跑的活。平台不在那条链路上（CLI 直接跟
  provider 对话），所以用量得**从 CLI 的原始输出里解析**出来。

这里钉住的是：

* 解析器认得三种形状；认不出就是**没有**（不写 0，不猜）；
* 同一条 usage 在流里出现多次**不重复计**（取 CLI 自己报的整场汇总）；
* 输入一律**含缓存**归一 —— 各家的「命中率」才是同一个口径；
* 记账写进「成员自报」那本账，**不混进**额度池那本；
* attempt 结束（提交结果）之后，账上多一笔。
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from app.claim.service import ClaimRequest, ClaimService
from app.config import settings
from app.git.repo import CanonicalRepo
from app.git.worktree import WorktreeManager
from app.pool.service import PoolService
from app.tasks.service import SchedulerService
from app.usage.cli_reported import (
    parse_cli_usage,
    read_reported_records,
    record_reported_usage,
    reported_ledger_path,
)
from tests.claim_support import make_database, seed_project, seed_task


def _now_iso() -> str:
    """一小时前（时间线只给最近 24 小时）。"""
    return (datetime.now(UTC) - timedelta(hours=1)).isoformat()


def _claim(database, *, request_id="cr-1", task_id="T-1"):
    return (
        ClaimService(database)
        .claim(
            ClaimRequest(claim_request_id=request_id, harness_id="harness-1", task_ids=(task_id,))
        )
        .items[0]
    )


def _worktree_rig(tmp_path):
    """claim -> start = in_progress with a real worktree."""
    database = make_database(tmp_path)
    repo = CanonicalRepo(tmp_path / "canonical.git")
    repo.ensure()
    base = repo.ref_commit(repo.integration_ref)
    assert base is not None
    manager = WorktreeManager(database, repo, root=tmp_path / "wt")
    service = SchedulerService(database)
    seed_project(database, integration_commit=base)
    seed_task(database, task_id="T-1")
    item = _claim(database)
    assert item.outcome == "claimed"
    service.start_task(
        task_id="T-1",
        attempt_id=item.attempt_id,
        fencing_epoch=item.fencing_epoch,
        group_short="g1",
        worktrees=manager,
    )
    return database, service, item


# ------------------------------------------------------------------- 解析器


def test_the_clis_own_summary_wins_over_summing_the_stream():
    """同一条 usage 在流里出现多次 —— 逐条累加会把同一份算上好几遍。

    真数据实测：Claude Code 那次，逐条累加是 CLI 自己汇总的 4 倍。所以取**汇总**。
    """
    stream = "\n".join(
        [
            json.dumps(
                {
                    "type": "assistant",
                    "uuid": "u1",
                    "message": {
                        "id": "m1",
                        "usage": {
                            "input_tokens": 22_977,
                            "cache_read_input_tokens": 0,
                            "output_tokens": 0,
                        },
                    },
                }
            ),
            json.dumps(
                {
                    "type": "assistant",
                    "uuid": "u2",
                    "message": {
                        "id": "m1",
                        "usage": {
                            "input_tokens": 22_977,
                            "cache_read_input_tokens": 0,
                            "output_tokens": 0,
                        },
                    },
                }
            ),
            json.dumps(
                {
                    "type": "result",
                    "total_cost_usd": 1.74,
                    "duration_api_ms": 640_181,
                    "usage": {
                        "input_tokens": 49_818,
                        "cache_read_input_tokens": 1_182_464,
                        "output_tokens": 35_543,
                    },
                }
            ),
        ]
    )
    usage = parse_cli_usage(stream)
    assert usage is not None
    assert usage.shape == "summary"
    # 总输入含缓存：新输入 + 缓存读
    assert usage.prompt_tokens == 49_818 + 1_182_464
    assert usage.completion_tokens == 35_543
    assert usage.cached_tokens == 1_182_464
    assert usage.cache_hit_rate is not None
    assert round(usage.cache_hit_rate, 3) == round(1_182_464 / 1_232_282, 3)


def test_opencode_steps_are_summed_once():
    """opencode 每步一条 tokens —— 同一步在流里出现两次只算一次。"""
    step = {
        "type": "step_finish",
        "part": {
            "id": "p1",
            "tokens": {"input": 126, "output": 48, "cache": {"read": 247_838, "write": 0}},
        },
    }
    stream = "\n".join(
        [
            json.dumps(step),
            json.dumps(step),
            json.dumps({**step, "part": {**step["part"], "id": "p2"}}),
        ]
    )
    usage = parse_cli_usage(stream)
    assert usage is not None
    assert usage.shape == "steps"
    assert usage.requests == 2
    assert usage.cached_tokens == 247_838 * 2
    assert usage.prompt_tokens == (126 + 247_838) * 2
    assert usage.completion_tokens == 48 * 2


def test_the_openai_shapes_are_read():
    chat = parse_cli_usage(
        json.dumps(
            {
                "usage": {
                    "prompt_tokens": 100,
                    "completion_tokens": 20,
                    "prompt_tokens_details": {"cached_tokens": 70},
                }
            }
        )
    )
    assert chat is not None
    assert (chat.prompt_tokens, chat.completion_tokens, chat.cached_tokens) == (100, 20, 70)

    responses = parse_cli_usage(
        json.dumps(
            {
                "usage": {
                    "input_tokens": 100,
                    "output_tokens": 20,
                    "input_tokens_details": {"cached_tokens": 70},
                }
            }
        )
    )
    assert responses is not None
    assert (responses.prompt_tokens, responses.completion_tokens, responses.cached_tokens) == (
        100,
        20,
        70,
    )


def test_a_stream_without_usage_is_no_usage():
    """认不出就是没有 —— 不写 0，不猜。"""
    assert parse_cli_usage("hello world") is None
    assert parse_cli_usage("") is None
    assert parse_cli_usage(json.dumps({"type": "assistant", "message": {"id": "m"}})) is None


# ------------------------------------------------------------------- 记账


def _write_cli_log(data_dir: Path, task_id: str, attempt_id: str, text: str) -> None:
    path = data_dir / "formal-runs" / f"{task_id}.{attempt_id}.log"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_recording_writes_the_reported_ledger(tmp_path):
    data_dir = tmp_path / "data"
    _write_cli_log(
        data_dir,
        "T-1",
        "T-1.a1",
        json.dumps(
            {
                "type": "result",
                "total_cost_usd": 0.5,
                "usage": {"input_tokens": 100, "cache_read_input_tokens": 900, "output_tokens": 20},
            }
        ),
    )
    usage = record_reported_usage(
        data_dir=data_dir, task_id="T-1", attempt_id="T-1.a1", harness_id="harness-1"
    )
    assert usage is not None
    assert usage.cached_tokens == 900
    records = read_reported_records(data_dir)
    assert len(records) == 1
    assert records[0]["consumer"] == "harness/harness-1"
    assert records[0]["cached_tokens"] == 900
    assert reported_ledger_path(data_dir).exists()


def test_a_usage_of_all_zeros_is_not_recorded(tmp_path):
    """全是 0 —— 0 的意思是「没数到」，不是「没用」。记下来会变成假话。"""
    data_dir = tmp_path / "data"
    _write_cli_log(
        data_dir,
        "T-1",
        "T-1.a1",
        json.dumps(
            {
                "type": "result",
                "total_cost_usd": 0,
                "usage": {"input_tokens": 0, "cache_read_input_tokens": 0, "output_tokens": 0},
            }
        ),
    )
    assert (
        record_reported_usage(
            data_dir=data_dir, task_id="T-1", attempt_id="T-1.a1", harness_id="harness-1"
        )
        is None
    )
    assert read_reported_records(data_dir) == []


def test_recording_without_a_usage_leaves_no_record(tmp_path):
    """CLI 没报用量 —— 账上什么也不该多。"""
    data_dir = tmp_path / "data"
    _write_cli_log(data_dir, "T-1", "T-1.a1", "no usage here")
    assert (
        record_reported_usage(
            data_dir=data_dir, task_id="T-1", attempt_id="T-1.a1", harness_id="harness-1"
        )
        is None
    )
    assert read_reported_records(data_dir) == []


def test_the_summary_keeps_the_two_ledgers_apart(tmp_path):
    """两笔账分开摆：额度池的数不掺成员自报的，成员自报的也不混进额度来源。"""
    data_dir = tmp_path / "data"
    pool_usage = data_dir / "pool" / "usage.jsonl"
    pool_usage.parent.mkdir(parents=True, exist_ok=True)
    pool_usage.write_text(
        json.dumps(
            {
                "at": "2026-10-25T09:00:00+00:00",
                "model": "gpt-5",
                "upstream": "codex:geekspace",
                "protocol": "openai_responses",
                "status": "ok",
                "duration_ms": 1000,
                "prompt_tokens": 10,
                "completion_tokens": 5,
                "cached_tokens": 2,
                "usage_reported": True,
                "consumer": "harness/harness-1",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    reported = data_dir / "pool" / "usage-reported.jsonl"
    reported.write_text(
        json.dumps(
            {
                "at": _now_iso(),
                "model": "cli",
                "upstream": "cli:harness-1",
                "protocol": "cli",
                "status": "reported",
                "duration_ms": 0,
                "prompt_tokens": 1000,
                "completion_tokens": 20,
                "cached_tokens": 900,
                "usage_reported": True,
                "consumer": "harness/harness-1",
            }
        )
        + "\n",
        encoding="utf-8",
    )

    service = PoolService(usage_path=pool_usage)
    summary = service.usage_summary()

    spent = summary["consumers"]["harness/harness-1"]
    # 额度池那本：只有池子记的数
    assert spent["prompt_tokens"] == 10
    assert spent["completion_tokens"] == 5
    assert spent["cached_tokens"] == 2
    # 成员自报的那本：另起一栏
    assert spent["reported"]["prompt_tokens"] == 1000
    assert spent["reported"]["completion_tokens"] == 20
    assert spent["reported"]["cached_tokens"] == 900
    assert spent["reported"]["requests"] == 1
    assert round(spent["reported"]["cache_hit_rate"], 3) == 0.9
    # 额度来源那边**不掺**成员自报的
    assert "cli:harness-1" not in summary["upstreams"]
    assert set(summary["upstreams"]) == {"codex:geekspace"}
    assert summary["reported_requests"] == 1

    # 时间线也分开分桶
    timeline = service.usage_timeline(hours=24)
    buckets = [b for b in timeline["buckets"] if b["reported_consumer_tokens"]]
    assert buckets, "自报的那部分也要按小时分桶"
    assert buckets[-1]["reported_consumer_tokens"]["harness/harness-1"]["cached_tokens"] == 900
    assert buckets[-1]["consumer_tokens"] == {}, "额度池的小桶不该混进自报的数"


def test_the_attempt_end_records_the_reported_usage(tmp_path, monkeypatch):
    """提交结果之后，这一轮的用量记进「成员自报」那本账。"""
    data_dir = tmp_path / "data"
    monkeypatch.setattr(settings, "data_dir", str(data_dir))
    database, service, item = _worktree_rig(tmp_path)
    _write_cli_log(
        data_dir,
        "T-1",
        item.attempt_id,
        json.dumps(
            {
                "type": "result",
                "total_cost_usd": 0.25,
                "usage": {"input_tokens": 200, "cache_read_input_tokens": 800, "output_tokens": 30},
            }
        ),
    )
    service.submit_result(
        task_id="T-1",
        attempt_id=item.attempt_id,
        fencing_epoch=item.fencing_epoch,
        completion={"commit": "c-1", "harness_output": {"final_message": "done"}},
    )
    records = read_reported_records(data_dir)
    assert len(records) == 1
    assert records[0]["consumer"] == "harness/harness-1"
    assert records[0]["cached_tokens"] == 800
    assert records[0]["prompt_tokens"] == 1000
