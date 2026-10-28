"""Subject tokens and the API-layer permission matrix (auth-layer card).

``tokens.py`` owns issuance/verification/revocation; ``policy.py`` owns the
single ``decide()`` function mirroring R10 §17; ``app/api/auth_deps.py`` wires
both into FastAPI and writes the ``permission_denied`` audit event on every
refusal. ``python -m app.auth`` is the operator CLI.
"""

from __future__ import annotations

from app.auth.policy import POLICY, Decision, decide
from app.auth.tokens import (
    TOKEN_PREFIX,
    AmbiguousFingerprint,
    AuthTokenError,
    Principal,
    TokenInvalid,
    TokenService,
    UnknownHarness,
    hash_token,
)

__all__ = [
    "POLICY",
    "TOKEN_PREFIX",
    "AmbiguousFingerprint",
    "AuthTokenError",
    "Decision",
    "Principal",
    "TokenInvalid",
    "TokenService",
    "UnknownHarness",
    "decide",
    "hash_token",
]
