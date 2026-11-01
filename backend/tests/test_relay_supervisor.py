"""转接由管理员守着这条线：在不在跑、要不要拉起来、起不来时说得清。

这一层不连平台、不起真进程（spawn 由测试注入），把三条口径钉死：
幂等（已经在跑不重复起）、不代笔（没有 relay 自己的凭据就不动手）、
"起来了"要用心跳复核，不能只凭 spawn 成功。
"""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from app.relay.supervisor import (
    HEARTBEAT_NAME,
    PID_NAME,
    RUNNING_WINDOW_SECONDS,
    ensure_relay_worker,
    read_status,
    relay_enabled,
)


def _mailbox(tmp_path: Path) -> Path:
    root = tmp_path / "relay"
    (root / "outbox").mkdir(parents=True)
    return root


def _token(tmp_path: Path) -> Path:
    token = tmp_path / "relay.key"
    token.write_text("demo-token\n", encoding="utf-8")
    return token


def _beat(root: Path, at: float | None = None) -> None:
    heartbeat = root / HEARTBEAT_NAME
    heartbeat.write_text("2026-11-02T00:00:00+00:00", encoding="utf-8")
    stamp = time.time() if at is None else at
    os.utime(heartbeat, (stamp, stamp))


def test_a_fresh_heartbeat_means_running_and_nothing_is_spawned(tmp_path):
    root = _mailbox(tmp_path)
    _beat(root)
    spawned: list[list[str]] = []

    result = ensure_relay_worker(
        root=root,
        group="G-1",
        token_file=_token(tmp_path),
        spawner=lambda command, _root: spawned.append(command) or 111,
    )

    assert result["running"] is True
    assert result["started"] is False
    # 两个 worker 同时读 outbox 会把同一封信投两遍：已经在跑就一定不能再起一个。
    assert spawned == []


def test_an_old_heartbeat_gets_the_worker_started(tmp_path):
    root = _mailbox(tmp_path)
    _beat(root, at=time.time() - RUNNING_WINDOW_SECONDS - 60)
    commands: list[list[str]] = []

    def spawner(command, mailbox_root):
        commands.append(command)
        _beat(mailbox_root)  # 真 worker 起来后会立刻打心跳
        (mailbox_root / PID_NAME).write_text("4242", encoding="utf-8")
        return 4242

    result = ensure_relay_worker(
        root=root, group="G-1", admins=["codex-window-admin"], token_file=_token(tmp_path), spawner=spawner
    )

    assert result["started"] is True
    assert result["running"] is True
    assert result["pid"] == 4242
    assert len(commands) == 1
    # 命令行里必须带上 relay 自己的凭据与群号：管理员只是启动它，不替它说话。
    assert "--token-file" in commands[0]
    assert "G-1" in commands[0]
    assert "--admin" in commands[0] and "codex-window-admin" in commands[0]


def test_without_the_relays_own_credential_nothing_is_started(tmp_path):
    """没有 relay 的凭据就不能动手 —— 用管理员的令牌顶上去等于平台替它说话。"""
    root = _mailbox(tmp_path)
    spawned: list[list[str]] = []

    result = ensure_relay_worker(
        root=root,
        group="G-1",
        token_file=tmp_path / "missing.key",
        spawner=lambda command, _root: spawned.append(command) or 1,
    )

    assert result["started"] is False
    assert "凭据" in result["note"]
    assert spawned == []


def test_a_process_that_never_beats_is_reported_as_started_but_not_delivering(tmp_path, monkeypatch):
    """spawn 成功不等于在投递：心跳没来就说没来，别把人哄过去。"""
    from app.relay import supervisor as module

    monkeypatch.setattr(module, "STARTUP_WAIT_SECONDS", 0.3)
    root = _mailbox(tmp_path)
    result = ensure_relay_worker(
        root=root,
        group="G-1",
        token_file=_token(tmp_path),
        spawner=lambda command, _root: 999,  # 起了，但从不打心跳
    )

    assert result["started"] is True
    assert result["running"] is False
    assert "心跳" in result["note"]


def test_a_live_process_without_a_heartbeat_is_not_started_twice(tmp_path, monkeypatch):
    """第一次启动要几秒才打心跳。这段空窗里再加一个，两个 worker 会把同一封信投两遍。"""
    from app.relay import supervisor as module

    root = _mailbox(tmp_path)
    (root / PID_NAME).write_text("4242", encoding="utf-8")
    monkeypatch.setattr(module, "_pid_alive", lambda pid: pid == 4242)
    spawned: list[list[str]] = []

    result = ensure_relay_worker(
        root=root,
        group="G-1",
        token_file=_token(tmp_path),
        spawner=lambda command, _root: spawned.append(command) or 1,
    )

    assert result["started"] is False
    assert result["running"] is False
    assert "启动" in result["note"]
    assert spawned == []


def test_a_spawn_failure_says_why(tmp_path):
    def boom(command, _root):
        raise OSError("python not found")

    result = ensure_relay_worker(root=_mailbox(tmp_path), group="G-1", token_file=_token(tmp_path), spawner=boom)

    assert result["running"] is False
    assert result["started"] is False
    assert "起不来" in result["note"]


def test_status_without_a_heartbeat_is_not_running(tmp_path):
    status = read_status(_mailbox(tmp_path))
    assert status.running is False
    assert status.age_seconds is None


def test_relay_enabled_follows_the_flag(monkeypatch):
    for value in ("1", "true", "YES", "on"):
        monkeypatch.setenv("AP_RELAY_MODE", value)
        assert relay_enabled() is True
    for value in ("", "0", "off"):
        monkeypatch.setenv("AP_RELAY_MODE", value)
        assert relay_enabled() is False


@pytest.mark.parametrize("flag", ["1"])
def test_mailbox_root_and_token_come_from_the_environment(monkeypatch, tmp_path, flag):
    from app.relay.supervisor import default_root, default_token_file

    monkeypatch.setenv("AP_RELAY_MODE", flag)
    monkeypatch.setenv("AP_RELAY_ROOT", str(tmp_path / "elsewhere"))
    monkeypatch.setenv("AP_RELAY_TOKEN_FILE", str(tmp_path / "other.key"))
    # 界面（relay/overview 走 OFFICE_RELAY_ROOT）与 worker 必须指同一个信箱，
    # 否则界面读的是另一份，会显示"没有线程"而信其实在别处。
    assert default_root() == tmp_path / "elsewhere"
    assert default_token_file() == tmp_path / "other.key"
