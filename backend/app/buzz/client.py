"""Buzz relay WS 发布客户端：连接 → NIP-42 AUTH → kind:9 逐条发布（buzz-bridge ④）。

只写不读（ADR-DEV-005 决策 2）：本客户端**不发送 REQ/CLOSE**，也不消费任何
入站业务数据；等待的唯一合法回复是 AUTH challenge 与 OK。超时、断连、
OK=false（被 relay 拒绝）一律抛 :class:`BuzzRelayError` 交给游标层——游标只在
整批 OK 之后才推进。

逐条「发一条、等一条 OK」：顺序与 `task_events.seq` 一致，失败定位到具体事件
id；批量并发留到真有机会量需求时再谈（ADR 回退条件 3 同款处置）。
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Sequence

import websockets
from websockets.exceptions import WebSocketException

from app.buzz.nostr import NostrEvent, Signer

DEFAULT_TIMEOUT_SECONDS = 10.0


class BuzzRelayError(RuntimeError):
    """连接/认证/发布层面的失败；游标层据此保持游标不动。"""


async def _read_frame(ws, timeout: float, what: str) -> list[object]:
    try:
        raw = await asyncio.wait_for(ws.recv(), timeout=timeout)
    except TimeoutError as exc:
        raise BuzzRelayError(f"timed out waiting for {what}") from exc
    try:
        frame = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise BuzzRelayError(f"relay sent a non-JSON frame: {str(raw)[:120]}") from exc
    if not isinstance(frame, list) or not frame:
        raise BuzzRelayError(f"relay sent a malformed frame: {str(raw)[:120]}")
    return frame


async def _await_challenge(ws, timeout: float) -> str:
    while True:
        frame = await _read_frame(ws, timeout, "the NIP-42 AUTH challenge")
        if frame[0] == "AUTH" and len(frame) >= 2 and isinstance(frame[1], str):
            return frame[1]
        # 其它帧（NOTICE 等）不构成授权信息，继续等 challenge


async def _await_ok(ws, event_id: str, timeout: float) -> tuple[bool, str]:
    while True:
        frame = await _read_frame(ws, timeout, f"OK for event {event_id[:12]}")
        if frame[0] == "OK" and len(frame) >= 3 and str(frame[1]) == event_id:
            detail = str(frame[3]) if len(frame) >= 4 else ""
            return bool(frame[2]), detail


async def publish_batch(
    relay_url: str,
    signer: Signer,
    events: Sequence[NostrEvent],
    *,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
) -> None:
    try:
        async with websockets.connect(relay_url, open_timeout=timeout) as ws:
            challenge = await _await_challenge(ws, timeout)
            auth = signer.auth_event(relay_url=relay_url, challenge=challenge)
            await ws.send(json.dumps(["AUTH", auth.as_dict()], separators=(",", ":")))
            accepted, detail = await _await_ok(ws, auth.id, timeout)
            if not accepted:
                raise BuzzRelayError(f"relay rejected NIP-42 AUTH: {detail or 'no reason given'}")
            for event in events:
                await ws.send(json.dumps(["EVENT", event.as_dict()], separators=(",", ":")))
                accepted, detail = await _await_ok(ws, event.id, timeout)
                if not accepted:
                    raise BuzzRelayError(
                        f"relay rejected event {event.id[:12]}: {detail or 'no reason given'}"
                    )
    except (OSError, TimeoutError, WebSocketException) as exc:
        # 连接被拒/超时/中断：统一给游标层一个可判定的失败（游标不动）
        raise BuzzRelayError(f"relay connection failed: {type(exc).__name__}: {exc}") from exc


def publish_events(
    relay_url: str,
    signer: Signer,
    events: Sequence[NostrEvent],
    *,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
) -> None:
    """同步入口（桥/CLI 侧全同步，DB 与游标代码不染 asyncio）。"""
    asyncio.run(publish_batch(relay_url, signer, events, timeout=timeout))
