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
    AmbiguousFingerprint,
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


def test_revoke_fingerprint_revokes_exactly_the_one_it_names(tmp_path):
    """凭据明文已经不在手上时，指纹是唯一还能指认它的东西。"""
    database = make_database(tmp_path)
    register_harness(database, "h-A")
    service = TokenService(database)
    stale = service.issue(subject_type="harness", subject_id="h-A", label="old")
    current = service.issue(subject_type="harness", subject_id="h-A", label="new")

    assert service.revoke_fingerprint(fingerprint=hash_token(stale)[:19], reason="rotated") is True

    with pytest.raises(TokenInvalid, match="token_revoked"):
        service.verify(stale)
    assert service.verify(current).subject_id == "h-A"
    # 再吊销一次是 no-op，不存在的指纹同样不是"吊销成功"
    assert service.revoke_fingerprint(fingerprint=hash_token(stale)[:19]) is False
    assert service.revoke_fingerprint(fingerprint="sha256:" + "0" * 12) is False


def test_revoke_fingerprint_refuses_a_prefix_that_matches_two_tokens(tmp_path):
    """指纹是哈希前缀：撞上两条就拒绝，绝不猜 —— 吊销错一条会让能用的窗口下线。"""
    database = make_database(tmp_path)
    service = TokenService(database)
    with database.transaction() as conn:
        for tail in ("a1", "a2"):
            conn.execute(
                "INSERT INTO auth_tokens(token_hash, subject_type, subject_id, label,"
                " created_at, revoked_at, revoked_reason)"
                " VALUES (?, 'human', 'operator', 'built', '2026-01-01T00:00:00+00:00', NULL, NULL)",
                (f"sha256:deadbeef{tail}" + "0" * 52,),
            )

    with pytest.raises(AmbiguousFingerprint):
        service.revoke_fingerprint(fingerprint="sha256:deadbeef")
    assert len(service.list_tokens(subject_type="human")) == 2  # 一条都没动

    # 多给几个字符就能落到唯一一条上
    assert service.revoke_fingerprint(fingerprint="sha256:deadbeefa1") is True


def test_revoke_fingerprint_checks_its_own_shape(tmp_path):
    service = TokenService(make_database(tmp_path))

    with pytest.raises(AuthTokenError, match="fingerprint must look like"):
        service.revoke_fingerprint(fingerprint="apt_deadbeef")


def test_cli_revokes_by_fingerprint_without_the_plaintext(tmp_path, monkeypatch, capsys):
    database = make_database(tmp_path)
    register_harness(database, "h-A")
    monkeypatch.setattr(settings, "db_path", database.path)
    raw = TokenService(database).issue(subject_type="harness", subject_id="h-A")

    assert auth_cli.main(["revoke", "--fingerprint", hash_token(raw)[:19], "--id", "h-A"]) == 0
    assert json.loads(capsys.readouterr().out.strip())["revoked"] is True
    # 读不懂的指纹是"拒绝"（2），不是"没有这条"（1）
    assert auth_cli.main(["revoke", "--fingerprint", "nonsense"]) == 2
    assert "fingerprint must look like" in json.loads(capsys.readouterr().out.strip())["refused"]
