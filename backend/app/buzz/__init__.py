"""Buzz 单向桥（ADR-DEV-005）：平台事件只写发布到 Buzz 频道，绝不回读。

模块分工：

* :mod:`app.buzz.nostr` —— 最小 NIP-01/NIP-42 事件构造与 BIP340 签名；
* :mod:`app.buzz.projection` —— 事件 → 展示消息投影（发布集从 R10 §37.7 推导）；
* :mod:`app.buzz.client` —— WS + NIP-42 AUTH 的发布客户端（只写不读）；
* :mod:`app.buzz.bridge` —— 游标、批次、no-op/拒绝语义（形状同 `app.alerts`）。
"""

from app.buzz.bridge import BuzzBridge, BuzzConfigError, BuzzRunReport

__all__ = ["BuzzBridge", "BuzzConfigError", "BuzzRunReport"]
