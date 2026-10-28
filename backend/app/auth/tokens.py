"""Subject tokens for the API-layer permission matrix (R10 §17, auth-layer).

Design choices live in the migration header (012_auth_tokens.sql); the short
version: opaque random tokens, only SHA-256 hashes stored, revocation is a row
update, and the plaintext is printed exactly once by the issuing CLI.

An issuance for a ``harness`` subject must name a harness the human-maintained
registry already knows (R10 §6: self-report is not authorization). Human
subjects have no registry to check against in v1.
"""

from __future__ import annotations

import hashlib
import re
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime

from app.adapter.registry import HarnessRegistry
from app.db.connection import Database

#: Wire prefix so a scanned token is recognisable in logs/reports.
TOKEN_PREFIX = "apt_"

SUBJECT_TYPES = ("harness", "human")


class AuthTokenError(ValueError):
    """Issuance or revocation was refused."""


class UnknownHarness(AuthTokenError):
    """The harness is not in the human-maintained registry."""


class AmbiguousFingerprint(AuthTokenError):
    """A fingerprint matched more than one token, so nothing was revoked."""


class TokenInvalid(LookupError):
    """The presented token is unknown or revoked.

    ``reason`` is a stable string for the audit event:
    ``token_missing`` / ``token_invalid`` / ``token_revoked``.
    """

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True, slots=True)
class Principal:
    """Who a verified request is acting as."""

    subject_type: str
    subject_id: str
    token_label: str = ""


def hash_token(raw: str) -> str:
    return "sha256:" + hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _now() -> str:
    return datetime.now(UTC).isoformat()


class TokenService:
    def __init__(self, database: Database) -> None:
        self.database = database

    def issue(self, *, subject_type: str, subject_id: str, label: str = "") -> str:
        """Create a token and return its plaintext once."""
        if subject_type not in SUBJECT_TYPES:
            raise AuthTokenError(f"unknown subject type {subject_type!r}; use {SUBJECT_TYPES}")
        subject_id = subject_id.strip()
        if not subject_id:
            raise AuthTokenError("subject id must not be empty")
        if subject_type == "harness" and HarnessRegistry(self.database).current(subject_id) is None:
            raise UnknownHarness(
                f"harness {subject_id!r} is not in the registry; register it before issuing a token"
            )
        raw = TOKEN_PREFIX + secrets.token_hex(32)
        with self.database.transaction() as conn:
            conn.execute(
                "INSERT INTO auth_tokens(token_hash, subject_type, subject_id, label,"
                " created_at, revoked_at, revoked_reason)"
                " VALUES (?, ?, ?, ?, ?, NULL, NULL)",
                (hash_token(raw), subject_type, subject_id, label, _now()),
            )
        return raw

    def verify(self, raw: str | None) -> Principal:
        """Resolve a presented token to its principal, or raise TokenInvalid."""
        if raw is None or not raw.strip():
            raise TokenInvalid("token_missing")
        rows = self.database.read(
            "SELECT subject_type, subject_id, label, revoked_at, revoked_reason"
            " FROM auth_tokens WHERE token_hash = ?",
            (hash_token(raw.strip()),),
        )
        if not rows:
            raise TokenInvalid("token_invalid")
        row = rows[0]
        if row["revoked_at"] is not None:
            raise TokenInvalid("token_revoked")
        return Principal(
            subject_type=str(row["subject_type"]),
            subject_id=str(row["subject_id"]),
            token_label=str(row["label"]),
        )

    def revoke(self, *, raw: str, reason: str = "") -> bool:
        """Revoke by plaintext. Returns False when the token is unknown."""
        with self.database.transaction() as conn:
            cursor = conn.execute(
                "UPDATE auth_tokens SET revoked_at = ?, revoked_reason = ?"
                " WHERE token_hash = ? AND revoked_at IS NULL",
                (_now(), reason, hash_token(raw.strip())),
            )
        return cursor.rowcount == 1

    def revoke_fingerprint(
        self,
        *,
        fingerprint: str,
        reason: str = "",
        subject_type: str | None = None,
        subject_id: str | None = None,
    ) -> bool:
        """Revoke one token by the fingerprint :meth:`list_tokens` prints.

        Revoking by plaintext only works for somebody who still holds the
        plaintext; when a ``data/<窗口>.key`` file is replaced the old token
        stays valid and there is nothing left to present.  The fingerprint is
        the only handle that survives that, so it is what the operator UI
        offers.

        The fingerprint is a *prefix* of the stored hash.  One that matches two
        rows is refused (``AmbiguousFingerprint``) rather than guessed --
        revoking the wrong credential takes a working window offline, and
        running windows accumulate a token per re-issuance.
        """
        prefix = fingerprint.strip()
        if not re.fullmatch(r"sha256:[0-9a-f]{6,64}", prefix):
            raise AuthTokenError("fingerprint must look like sha256:<hex> (6 to 64 hex chars)")
        sql = "SELECT token_hash FROM auth_tokens WHERE revoked_at IS NULL AND token_hash LIKE ?"
        params: list[object] = [prefix + "%"]
        if subject_type is not None:
            sql += " AND subject_type = ?"
            params.append(subject_type)
        if subject_id is not None:
            sql += " AND subject_id = ?"
            params.append(subject_id)
        rows = self.database.read(sql, tuple(params))
        if not rows:
            return False
        if len(rows) > 1:
            raise AmbiguousFingerprint(
                f"{len(rows)} tokens start with {prefix}; give more characters of the fingerprint"
            )
        with self.database.transaction() as conn:
            cursor = conn.execute(
                "UPDATE auth_tokens SET revoked_at = ?, revoked_reason = ?"
                " WHERE token_hash = ? AND revoked_at IS NULL",
                (_now(), reason, str(rows[0]["token_hash"])),
            )
        return cursor.rowcount == 1

    def list_tokens(
        self, *, subject_type: str | None = None, subject_id: str | None = None
    ) -> list[dict[str, object]]:
        """Rows without secrets (the hash is shortened to a fingerprint)."""
        sql = (
            "SELECT token_hash, subject_type, subject_id, label, created_at,"
            " revoked_at, revoked_reason FROM auth_tokens"
        )
        clauses: list[str] = []
        params: list[object] = []
        if subject_type is not None:
            clauses.append("subject_type = ?")
            params.append(subject_type)
        if subject_id is not None:
            clauses.append("subject_id = ?")
            params.append(subject_id)
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY created_at, token_hash"
        rows = self.database.read(sql, tuple(params))
        return [
            {
                "fingerprint": str(row["token_hash"])[:19],
                "subject_type": str(row["subject_type"]),
                "subject_id": str(row["subject_id"]),
                "label": str(row["label"]),
                "created_at": str(row["created_at"]),
                "revoked_at": None if row["revoked_at"] is None else str(row["revoked_at"]),
                "revoked_reason": (
                    None if row["revoked_reason"] is None else str(row["revoked_reason"])
                ),
            }
            for row in rows
        ]
