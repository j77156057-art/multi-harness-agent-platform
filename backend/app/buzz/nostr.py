"""最小 NIP-01 / NIP-42 事件实现（buzz-bridge；原生实现，不引 Nostr SDK）。

wire 事实来源：`docs/reports/paperclip-buzz-source-review.md` §1/§5（快照
commit 已记录）——频道 = NIP-29 group，消息 = **kind:9 且必须带
`["h", channel]` 标签**；接入 = WS + **NIP-42 AUTH**；`buzz-sdk` 的类型化
构造器**不持钥**（签名是调用方职责，本模块即那一职责的原生实现）。

签名 = BIP340 schnorr（coincurve/libsecp256k1）。aux 随机数显式取 **32 字节
零**：与 BIP340 官方测试向量 #0 **逐字节一致**（`test_buzz_bridge.py` 固化）；
零 aux 使签名对同一消息确定——重放同一批事件产出**同 id 同签名**的字节，
relay 按 id 去重即"恰好一次"的显示效果（ADR-DEV-005 决策 6）。

只写不读（ADR-DEV-005 决策 2）：本模块只构造出站事件；入站事件的解析仅剩
`verify_event`（测试 relay 用），没有任何订阅/REQ 路径。
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from coincurve import PrivateKey, PublicKeyXOnly

#: NIP-29 group 消息（Buzz 频道消息）
KIND_CHANNEL_MESSAGE = 9
#: NIP-42 relay 认证事件
KIND_AUTH = 22242


class BuzzKeyError(ValueError):
    """私钥文件缺失/非法。错误消息绝不包含密钥材料本身。"""


def event_id(
    pubkey: str, created_at: int, kind: int, tags: tuple[tuple[str, ...], ...], content: str
) -> str:
    """NIP-01 事件 id：sha256(canonical json([0, pubkey, created_at, kind, tags, content]))。"""
    serialized = json.dumps(
        [0, pubkey, int(created_at), int(kind), [list(tag) for tag in tags], content],
        ensure_ascii=False,
        separators=(",", ":"),
        allow_nan=False,
    )
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class NostrEvent:
    id: str
    pubkey: str
    created_at: int
    kind: int
    tags: tuple[tuple[str, ...], ...]
    content: str
    sig: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "pubkey": self.pubkey,
            "created_at": self.created_at,
            "kind": self.kind,
            "tags": [list(tag) for tag in self.tags],
            "content": self.content,
            "sig": self.sig,
        }


class Signer:
    """持有私钥的签名器。密钥只在内存，不进库/事件/日志/错误消息。"""

    def __init__(self, secret: bytes) -> None:
        if len(secret) != 32:
            raise BuzzKeyError("secp256k1 secret key must be 32 bytes")
        self._key = PrivateKey(secret)
        # BIP340 用 x-only 公钥（压缩公钥去 parity 前缀）
        self.pubkey: str = self._key.public_key.format(compressed=True)[1:].hex()

    @classmethod
    def from_hex(cls, text: str) -> Signer:
        normalized = text.strip()
        if len(normalized) != 64:
            raise BuzzKeyError("key file must contain a 64-char hex secp256k1 secret key")
        try:
            secret = bytes.fromhex(normalized)
        except ValueError as exc:
            raise BuzzKeyError("key file must contain a 64-char hex secp256k1 secret key") from exc
        return cls(secret)

    @classmethod
    def from_file(cls, path: Path) -> Signer:
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as exc:
            raise BuzzKeyError(f"cannot read buzz key file {path}: {exc}") from exc
        return cls.from_hex(text)

    def sign_digest(self, digest: bytes) -> bytes:
        """对 32 字节消息（= 事件 id 的字节）做 BIP340 签名。确定性（零 aux）。"""
        if len(digest) != 32:
            raise ValueError("BIP340 signs a 32-byte message digest")
        return self._key.sign_schnorr(digest, aux_randomness=bytes(32))

    def build(
        self,
        *,
        kind: int,
        content: str,
        tags: tuple[tuple[str, ...], ...],
        created_at: int,
    ) -> NostrEvent:
        digest_hex = event_id(self.pubkey, created_at, kind, tags, content)
        signature = self.sign_digest(bytes.fromhex(digest_hex)).hex()
        return NostrEvent(
            id=digest_hex,
            pubkey=self.pubkey,
            created_at=int(created_at),
            kind=int(kind),
            tags=tags,
            content=content,
            sig=signature,
        )

    def channel_message(self, *, channel: str, content: str, created_at: int) -> NostrEvent:
        """kind:9 + `["h", channel]`——Buzz 频道消息的必需形状。"""
        return self.build(
            kind=KIND_CHANNEL_MESSAGE,
            content=content,
            tags=(("h", channel),),
            created_at=created_at,
        )

    def auth_event(
        self, *, relay_url: str, challenge: str, created_at: int | None = None
    ) -> NostrEvent:
        """NIP-42 AUTH：kind:22242 + relay/challenge 两个 tag，content 为空。"""
        stamp = int(time.time()) if created_at is None else int(created_at)
        return self.build(
            kind=KIND_AUTH,
            content="",
            tags=(("relay", relay_url), ("challenge", challenge)),
            created_at=stamp,
        )


def verify_event(payload: dict[str, Any]) -> bool:
    """重算 id + schnorr 验签。畸形输入一律 False（不抛）。测试 relay 专用。"""
    try:
        pubkey = str(payload["pubkey"])
        signature = bytes.fromhex(str(payload["sig"]))
        claimed_id = str(payload["id"])
        tags = tuple(tuple(str(value) for value in tag) for tag in payload["tags"])
        recomputed = event_id(
            pubkey, int(payload["created_at"]), int(payload["kind"]), tags, str(payload["content"])
        )
        if recomputed != claimed_id:
            return False
        return PublicKeyXOnly(bytes.fromhex(pubkey)).verify(signature, bytes.fromhex(claimed_id))
    except (KeyError, TypeError, ValueError):
        return False
