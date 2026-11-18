"""池子的**声明文件**：人亲手写的档位，丢一次就没了。

背景（2026-11-19 真机复核）：``data/pool/config.json` 里同时有两段 ——
``cli_slots`` 与 ``upstream_tiers``，也就是「哪条额度免费、哪条按量计费」
都是**人说的**。以前的读法把「读不动」当成「没有声明」，紧接着的写回就把整份
文件覆盖成只剩自己那一段：另一段没了，且不留痕。

这与 2026-11-18 那次 ``user_keys`` 事故同构（``app/pool/user_keys.py`）——
同款形状、同款后果：丢的是「哪条额度花钱」这条**钱的门**。

这些用例钉住三件事：
1. 文件读不动/坏了 → **报错**，而且**一个字都不许改**；
2. 读不动时，写声明**不许**把别的声明段抹掉；
3. 读面（列档位）在文件读不动时**什么都不给**（fail-closed）：宁可少一条额度，
   也不能把收费的额度当成免费的摆上去。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.pool.sources import (
    PoolDeclarationError,
    declare_upstream_tier,
    declared_upstream_tiers,
    withdraw_upstream_tier,
)


def _write(path: Path, document: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(document, ensure_ascii=False, indent=2), encoding="utf-8")


def test_declaring_one_tier_keeps_the_other_section(tmp_path):
    """写一段**不许**抹掉另一段：那是两次不同的声明。"""
    path = tmp_path / "pool" / "config.json"
    _write(
        path,
        {
            "cli_slots": [
                {
                    "name": "codebuddy",
                    "model": "hy4flash-free",
                    "tier": "free",
                    "binary": "codebuddy",
                    "args": ["-p"],
                }
            ],
            "upstream_tiers": [{"name": "opencode:local", "tier": "free"}],
        },
    )

    declare_upstream_tier("opencode:minimax", "metered", config_path=path)

    document = json.loads(path.read_text(encoding="utf-8"))
    assert len(document["cli_slots"]) == 1
    assert {entry["name"] for entry in document["upstream_tiers"]} == {
        "opencode:local",
        "opencode:minimax",
    }


def test_a_broken_declaration_file_is_reported_and_left_alone(tmp_path):
    """文件坏了 → 报错，而且**一个字都不许改**。

    以前这里是「读不出来 = 没有声明」，写回就把整份文件覆盖成只剩自己那一段。
    """
    path = tmp_path / "pool" / "config.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    broken = '{"cli_slots": [{"name": "codebuddy"'
    path.write_text(broken, encoding="utf-8")

    with pytest.raises(PoolDeclarationError):
        declare_upstream_tier("opencode:local", "free", config_path=path)

    assert path.read_text(encoding="utf-8") == broken


def test_withdrawing_from_a_broken_file_is_reported_not_called_a_no(tmp_path):
    """撤不动的档位要说「读不动」，不能说「本来就没有」。"""
    path = tmp_path / "pool" / "config.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not json", encoding="utf-8")

    with pytest.raises(PoolDeclarationError):
        withdraw_upstream_tier("opencode:local", config_path=path)


def test_the_read_face_gives_nothing_when_the_file_is_broken(tmp_path):
    """读面 fail-closed：读不出来就什么都不给，让上游回到代码默认（metered）。

    绝不猜：把「读不动」猜成「免费」，等于把收费的额度当免费用。
    """
    path = tmp_path / "pool" / "config.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not json", encoding="utf-8")

    assert declared_upstream_tiers(path) == []


def test_the_write_keeps_a_backup(tmp_path):
    """写之前留一份 .bak：写丢的那一次看起来和写成功的一模一样。"""
    path = tmp_path / "pool" / "config.json"
    _write(path, {"upstream_tiers": [{"name": "opencode:local", "tier": "free"}]})

    declare_upstream_tier("opencode:minimax", "metered", config_path=path)

    backup = path.with_suffix(path.suffix + ".bak")
    assert backup.exists()
    old = json.loads(backup.read_text(encoding="utf-8"))
    assert {entry["name"] for entry in old["upstream_tiers"]} == {"opencode:local"}
