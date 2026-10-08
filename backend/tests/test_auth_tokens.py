"""Subject tokens: issuance, verification, revocation, CLI (auth-layer card).

The store-side half of DOC-AUTH-001: only hashes are persisted, revocation is
immediate, and issuance for a harness demands a registry row (R10 §6 --
self-report is not authorization).
"""

from __future__ import annotations

import json

import pytest

from app.adapter.protocol import HarnessRegistration
from app.adapter.registry import HarnessRegistry
from app.auth import __main__ as auth_cli
from app.auth.tokens import (
    TOKEN_PREFIX,
    AuthTokenError,
    TokenInvalid,
    TokenService,
    UnknownHarness,
    hash_token,
)
from app.config import settings
from tests.claim_support import make_database


def register_harness(database, harness_id: str) -> None:
    HarnessRegistry(database).register(
        HarnessRegistration(
            harness_id=harness_id,
            name=harness_id,
            provider="openai",
            adapter="codex",
            adapter_version="1.0",
            capabilities=("implementation",),
            registration_version="reg-1",
        )
    )


def test_issue_returns_plaintext_once_and_stores_only_a_hash(tmp_path):
    database = make_database(tmp_path)
    register_harness(database, "h-A")
    service = TokenService(database)

    raw = service.issue(subject_type="harness", subject_id="h-A", label="primary")

    assert raw.startswith(TOKEN_PREFIX)
    rows = database.read("SELECT token_hash FROM auth_tokens")
    stored = str(rows[0]["token_hash"])
    assert stored == hash_token(raw)
    assert raw not in stored  # the plaintext is nowhere in the row

    principal = service.verify(raw)
    assert principal.subject_id == "h-A"
    assert principal.subject_type == "harness"
    assert principal.token_label == "primary"


def test_verify_distinguishes_missing_invalid_and_revoked(tmp_path):
    database = make_database(tmp_path)
    service = TokenService(database)
    raw = service.issue(subject_type="human", subject_id="operator")

    with pytest.raises(TokenInvalid, match="token_missing"):
        service.verify(None)
    with pytest.raises(TokenInvalid, match="token_missing"):
        service.verify("   ")
    with pytest.raises(TokenInvalid, match="token_invalid"):
        service.verify(TOKEN_PREFIX + "0" * 64)

    assert service.revoke(raw=raw, reason="rotated") is True
    with pytest.raises(TokenInvalid, match="token_revoked"):
        service.verify(raw)
    # revoking twice is a no-op, and an unknown token is not "revoked"
    assert service.revoke(raw=raw) is False
    assert service.revoke(raw=TOKEN_PREFIX + "1" * 64) is False


def test_harness_issuance_requires_the_registry(tmp_path):
    database = make_database(tmp_path)
    service = TokenService(database)

    with pytest.raises(UnknownHarness):
        service.issue(subject_type="harness", subject_id="ghost")

    register_harness(database, "ghost")
    assert service.issue(subject_type="harness", subject_id="ghost").startswith(TOKEN_PREFIX)

    # humans have no registry to check against in v1
    assert service.issue(subject_type="human", subject_id="operator").startswith(TOKEN_PREFIX)


def test_issue_validates_subject_shape(tmp_path):
    database = make_database(tmp_path)
    service = TokenService(database)

    with pytest.raises(AuthTokenError, match="unknown subject type"):
        service.issue(subject_type="robot", subject_id="r-1")
    with pytest.raises(AuthTokenError, match="must not be empty"):
        service.issue(subject_type="human", subject_id="   ")


def test_list_tokens_shows_fingerprints_not_secrets(tmp_path):
    database = make_database(tmp_path)
    service = TokenService(database)
    raw = service.issue(subject_type="human", subject_id="operator", label="console")
    service.issue(subject_type="human", subject_id="other")

    rows = service.list_tokens(subject_type="human", subject_id="operator")

    assert len(rows) == 1
    assert rows[0]["label"] == "console"
    assert str(rows[0]["fingerprint"]).startswith("sha256:")
    assert raw not in json.dumps(rows)


def test_cli_issue_list_revoke(tmp_path, monkeypatch, capsys):
    database = make_database(tmp_path)
    register_harness(database, "h-A")
    monkeypatch.setattr(settings, "db_path", database.path)

    assert auth_cli.main(["issue", "--type", "harness", "--id", "h-A", "--label", "cli"]) == 0
    issued = json.loads(capsys.readouterr().out.strip())
    raw = issued["issued"]["token"]
    assert raw.startswith(TOKEN_PREFIX)
    assert issued["issued"]["enforcement"] in {"off", "enforce"}

    assert auth_cli.main(["list", "--type", "harness"]) == 0
    listing = json.loads(capsys.readouterr().out.strip())
    assert len(listing["tokens"]) == 1
    assert raw not in json.dumps(listing)

    assert auth_cli.main(["revoke", "--token", raw, "--reason", "done"]) == 0
    assert json.loads(capsys.readouterr().out.strip())["revoked"] is True

    # revoked already -> 1; unknown harness -> 2 (refused)
    assert auth_cli.main(["revoke", "--token", raw]) == 1
    capsys.readouterr()
    assert auth_cli.main(["issue", "--type", "harness", "--id", "ghost"]) == 2
    refused = json.loads(capsys.readouterr().out.strip())
    assert "not in the registry" in refused["refused"]
