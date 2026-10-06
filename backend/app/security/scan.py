"""Outbound secret scanning (R10 §17, MVP-11).

§17's boundary rule: bodies the API accepts -- completion, findings, message
text -- are scanned **before** anything is persisted. A hit rejects the write
and records a ``secret_scan_blocked`` event carrying position, rule and a hash
of the value -- never the value itself (MVP-08 prep D-8: the error body may
only carry ``scan_id`` and ``rule_name``; no fragments, no line numbers, no
context).

The rule table is deliberately conservative: high-signal token formats, plus
one ``key = value`` rule that skips pure-hex matches so commit hashes and
digests in legitimate content do not trip it. A false negative is a report
item; a false positive blocks real work.
"""

from __future__ import annotations

import hashlib
import re
import uuid
from dataclasses import dataclass
from typing import Any

SECURITY_CODE = "security.secret_scan_blocked"
MESSAGE_CODE = "message.secret_scan_blocked"

#: (rule_name, pattern). A pattern with a capture group hashes the captured
#: value; without one it hashes the whole match.
_RULES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("aws_access_key_id", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("private_key_block", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("github_token", re.compile(r"\b(?:ghp_[A-Za-z0-9]{36}|github_pat_[A-Za-z0-9_]{22,})\b")),
    ("slack_token", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b")),
    (
        "generic_credential",
        re.compile(
            r"(?i)\b(?:api[_-]?key|secret|password|passwd|token)\b\s*[=:]\s*[\"']?"
            r"([A-Za-z0-9_+/=.-]{20,})"
        ),
    ),
)

_HEX = re.compile(r"[0-9a-fA-F]+")


@dataclass(frozen=True, slots=True)
class SecretFinding:
    rule_name: str
    location: str
    value_hash: str

    def as_json(self) -> dict[str, str]:
        return {
            "rule_name": self.rule_name,
            "location": self.location,
            "value_hash": self.value_hash,
        }


class SecretScanBlocked(RuntimeError):
    """The scanned body contained a likely secret; nothing was persisted.

    The message is a static template: it must never echo the match (D-8).
    """

    def __init__(
        self,
        *,
        code: str,
        scan_id: str,
        rule_name: str,
        event_seq: int | None = None,
        task_id: str | None = None,
    ) -> None:
        super().__init__("content rejected by secret scan; nothing was persisted")
        self.code = code
        self.scan_id = scan_id
        self.rule_name = rule_name
        self.event_seq = event_seq
        self.task_id = task_id

    @property
    def details(self) -> dict[str, str]:
        return {"scan_id": self.scan_id, "rule_name": self.rule_name}


def _looks_like_hash(value: str) -> bool:
    return len(value) >= 32 and _HEX.fullmatch(value) is not None


def scan_text(text: str, *, location: str) -> list[SecretFinding]:
    findings: list[SecretFinding] = []
    for rule_name, pattern in _RULES:
        for match in pattern.finditer(text):
            value = match.group(1) if match.groups() else match.group(0)
            if rule_name == "generic_credential" and _looks_like_hash(value):
                continue
            digest = hashlib.sha256(value.encode("utf-8")).hexdigest()
            findings.append(SecretFinding(rule_name, location, digest))
    return findings


def scan_payload(payload: Any, *, location: str = "payload") -> list[SecretFinding]:
    """Walk a JSON-shaped structure, scanning every string leaf."""
    if isinstance(payload, str):
        return scan_text(payload, location=location)
    if isinstance(payload, dict):
        findings: list[SecretFinding] = []
        for key, value in payload.items():
            findings.extend(scan_payload(value, location=f"{location}.{key}"))
        return findings
    if isinstance(payload, list | tuple):
        findings = []
        for index, value in enumerate(payload):
            findings.extend(scan_payload(value, location=f"{location}[{index}]"))
        return findings
    return []


def scan_for_block(payload: Any, *, surface: str) -> tuple[str, list[SecretFinding]] | None:
    """``(scan_id, findings)`` when the payload must not be persisted, else None."""
    findings = scan_payload(payload, location=surface)
    if not findings:
        return None
    return f"scan-{uuid.uuid4().hex}", findings
