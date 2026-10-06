"""Security primitives (MVP-11).

Currently one job: the outbound secret scan of R10 §17 that guards every body
the API accepts (see :mod:`app.security.scan`).
"""

from app.security.scan import (
    MESSAGE_CODE,
    SECURITY_CODE,
    SecretFinding,
    SecretScanBlocked,
    scan_for_block,
    scan_payload,
    scan_text,
)

__all__ = [
    "MESSAGE_CODE",
    "SECURITY_CODE",
    "SecretFinding",
    "SecretScanBlocked",
    "scan_for_block",
    "scan_payload",
    "scan_text",
]
