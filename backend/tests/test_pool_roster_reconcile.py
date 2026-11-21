"""名册对账：探查完结后撤掉**连续缺席**的陈旧声明（保守优先）。

背景（2026-11-21 真机核实）：``codebuddy/hy3`` 登记还挂着，但 codebuddy 自报
的新名册里已经没有它 —— 名册消失、登记还在，人还能选中一个已死的模型
（10-29 的 qoder 断流同因）。

这些用例钉住四件事：
1. **两轮**连续探查成功且缺席 → 声明被自动撤 + 审计落盘；
2. 只缺**一轮** → 只标 stale（``missing_from_roster``），绝不撤；
3. 中间隔一轮探查失败 → 连续性被打破，计数不涨、不许撤；
4. 探查失败的那轮本身不产生任何缺席计数；
5. 模型在名册里 → 什么都不变；
6. 同一轮重跑（幂等）不重复计数；
7. 探测的收尾路径真的会把对账跑起来（``POST /pool/roster-probe`` 闭环）。

对账的状态与声明文件都落在 ``tmp_path``，不碰真实数据。
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import pytest

from app.pool import probe, reconcile
from app.pool.sources import _read_declarations

#: codebuddy 的内置槽（``DEFAULT_CLI_SLOTS``）在合并后也参与对账：名册里把
#: 它们都列上，只有被盯的那条缺席 —— 这样用例聚焦在被测的那条声明上。
CODEBUDDY_IN_BUILTINS = ("hy3", "minimax-m3.1-flash", "step-5-preview")


def _write_config(path: Path, slots: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"cli_slots": slots}, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def _config(tmp_path: Path) -> Path:
    """一台最小机器：codebuddy 一条人写声明（其余是内置槽）。"""
    path = tmp_path / "pool" / "config.json"
    _write_config(
        path,
        [
            {
                "name": "codebuddy",
                "model": "hy4flash-free",
                "tier": "free",
                "binary": "codebuddy",
                "args": ["-p"],
            }
        ],
    )
    return path


def _state(tmp_path: Path) -> Path:
    return tmp_path / "pool" / "reconcile.json"


def _entry(cli: str, status: str, models: list[str], *, error: str = "") -> dict[str, Any]:
    return {"cli": cli, "status": status, "models": models, "error": error, "seconds": 0.1}


def _codebuddy_roster(*without: str) -> list[str]:
    """codebuddy 的名册：内置的都在，除了被 ``without`` 点名的那几个。"""
    names = ["hy4flash-free", *CODEBUDDY_IN_BUILTINS]
    return [name for name in names if name not in without]


def _rounds(
    config: Path, state: Path, spec: list[tuple[str, list[str] | None]]
) -> list[dict[str, Any]]:
    """按 (round_id, 名册) 跑一串对账轮；名册为 None 表示探查失败（failed）。"""
    reports = []
    for marker, roster in spec:
        if roster is None:
            entry = _entry("codebuddy", "failed", [], error="RuntimeError: spawn failed")
        else:
            entry = _entry("codebuddy", "done", roster)
        reports.append(
            reconcile.reconcile_round(
                [entry],
                round_id=marker,
                config_path=config,
                state_path=state,
            )
        )
    return reports


def test_two_consecutive_absent_rounds_retract_and_leave_audit(tmp_path):
    """两轮 done + 缺席 → 声明被自动撤 + 审计落盘（撤了什么、何时、哪两轮）。"""
    config = _config(tmp_path)
    state = _state(tmp_path)
    roster = _codebuddy_roster("hy3")

    _rounds(config, state, [(1001.0, roster), (1002.0, roster)])

    audit = reconcile.retracted_log(state)
    assert len(audit) == 1
    assert audit[0]["cli"] == "codebuddy"
    assert audit[0]["model_id"] == "codebuddy/hy3"
    assert audit[0]["absent_rounds"] == 2
    assert audit[0]["removed"] is True
    assert audit[0]["rounds"] == ["1001.0", "1002.0"]
    assert audit[0]["reason"]

    # 声明真的没了：内置槽被按既有约定写上「关闭」的覆盖。
    document = _read_declarations(config)
    slots = {
        (str(s.get("name")), str(s.get("model")))
        for s in document["cli_slots"]
        if s.get("enabled") is False
    }
    assert ("codebuddy", "hy3") in slots
    # 撤的是它自己，别的不动。
    live = {
        (str(s.get("name")), str(s.get("model")))
        for s in document["cli_slots"]
        if s.get("enabled") is not False
    }
    assert ("codebuddy", "hy4flash-free") in live
    assert ("codebuddy", "hy3") not in live
    # 撤掉的不再算缺席。
    assert reconcile.stale_declarations(state) == {}


def test_one_absent_round_only_marks_stale_and_never_retracts(tmp_path):
    """一轮 done + 缺席 → 只标 stale 不撤（一轮的缺席可能是这家 CLI 抽风）。"""
    config = _config(tmp_path)
    state = _state(tmp_path)

    _rounds(config, state, [(2001.0, _codebuddy_roster("hy3"))])

    stale = reconcile.stale_declarations(state)
    assert stale["codebuddy/hy3"]["missing_from_roster"] is True
    assert stale["codebuddy/hy3"]["absent_rounds"] == 1
    assert reconcile.retracted_log(state) == []
    document = _read_declarations(config)
    live = {
        (str(s.get("name")), str(s.get("model")))
        for s in document["cli_slots"]
        if s.get("enabled") is not False
    }
    assert ("codebuddy", "hy4flash-free") in live
    assert all(s.get("enabled") is not False for s in document["cli_slots"])


def test_an_error_round_between_breaks_the_streak(tmp_path):
    """中间隔一轮 error 槽 → 连续性被打破，计数不涨、不许撤。"""
    config = _config(tmp_path)
    state = _state(tmp_path)
    roster = _codebuddy_roster("hy3")

    _rounds(config, state, [(3001.0, roster), (3002.0, None), (3003.0, roster)])

    # 失败那轮清零，第三轮从头数：1，不是 2。
    stale = reconcile.stale_declarations(state)
    assert stale["codebuddy/hy3"]["absent_rounds"] == 1
    assert reconcile.retracted_log(state) == []


def test_a_failed_probe_round_counts_nothing(tmp_path):
    """探查 error 轮不计数：失败 ≠ 模型没了。"""
    config = _config(tmp_path)
    state = _state(tmp_path)

    _rounds(config, state, [(4001.0, None)])

    assert reconcile.stale_declarations(state) == {}
    assert reconcile.retracted_log(state) == []
    # 声明文件一个字没动。
    document = _read_declarations(config)
    assert [str(s.get("model")) for s in document["cli_slots"]] == ["hy4flash-free"]


def test_a_model_in_the_roster_changes_nothing(tmp_path):
    """声明在名册里 → 无缺席、无撤档、声明文件不动。"""
    config = _config(tmp_path)
    state = _state(tmp_path)

    _rounds(config, state, [(5001.0, _codebuddy_roster())])

    assert reconcile.stale_declarations(state) == {}
    assert reconcile.retracted_log(state) == []
    document = _read_declarations(config)
    assert [str(s.get("model")) for s in document["cli_slots"]] == ["hy4flash-free"]


def test_rerunning_the_same_round_does_not_count_twice(tmp_path):
    """幂等：同一轮（round_id 相同）重复对账不重复计数、不重复撤。"""
    config = _config(tmp_path)
    state = _state(tmp_path)
    roster = _codebuddy_roster("hy3")
    entry = _entry("codebuddy", "done", roster)

    first = reconcile.reconcile_round(
        [entry], round_id=6001.0, config_path=config, state_path=state
    )
    second = reconcile.reconcile_round(
        [entry], round_id=6001.0, config_path=config, state_path=state
    )

    assert first["absent"].get("codebuddy/hy3") == 1
    assert second["repeated"] is True
    assert second["retracted"] == []
    stale = reconcile.stale_declarations(state)
    assert stale["codebuddy/hy3"]["absent_rounds"] == 1


def test_the_probe_round_closes_with_a_reconcile(tmp_path, monkeypatch):
    """闭环：start_roster_probe 整轮完结后，收尾路径把对账真的跑起来。"""
    config = _config(tmp_path)
    state = _state(tmp_path)
    # 声明文件与状态文件都指到 tmp：探测线程读的是模块级函数。
    monkeypatch.setattr(reconcile, "pool_config_path", lambda: config)
    monkeypatch.setattr(reconcile, "pool_config_write_path", lambda: config)
    monkeypatch.setattr(reconcile, "reconcile_state_path", lambda: state)
    # 只探这一家（内置的 opencode/qoder 也有名册，别让它们混进这轮）。
    monkeypatch.setattr(
        probe,
        "_slots_with_roster",
        lambda: [
            {
                "name": "codebuddy",
                "model": "hy4flash-free",
                "tier": "free",
                "binary": "codebuddy",
                "args": ["-p"],
            }
        ],
    )

    # 假探查：codebuddy 每轮都 done，但名册里没有 hy3（不 spawn 真 CLI）。
    monkeypatch.setattr(
        probe,
        "_probe_one",
        lambda slot: _entry("codebuddy", "done", _codebuddy_roster("hy3")),
    )

    for _ in range(2):
        probe.start_roster_probe()
        deadline = time.monotonic() + 5.0
        while probe.snapshot()["running"] and time.monotonic() < deadline:
            time.sleep(0.01)
        assert not probe.snapshot()["running"]

    report = probe.snapshot().get("reconcile")
    assert isinstance(report, dict)
    assert report.get("repeated") is False
    audit = reconcile.retracted_log(state)
    assert len(audit) == 1
    assert audit[0]["model_id"] == "codebuddy/hy3"


@pytest.mark.parametrize(
    ("entry", "trusted"),
    [
        (_entry("codebuddy", "done", ["hy3"]), True),
        (_entry("codebuddy", "done", ["hy3"], error="boom"), False),
        (_entry("codebuddy", "empty", []), False),
        (_entry("codebuddy", "failed", []), False),
    ],
)
def test_only_a_clean_done_round_is_trusted(entry: dict[str, Any], trusted: bool):
    """只有 done 且 error 为空的槽可信 —— 这是缺席计数的**唯一**前提。"""
    rosters = reconcile._trusted_rosters([entry])
    assert ("codebuddy" in rosters) is trusted
