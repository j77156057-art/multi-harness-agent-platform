"""Claim prerequisites are named, not dressed up as system failures.

A fresh clone that starts the backend without a worker (``start.bat``) and then
claims a task used to blow up inside ``subprocess`` with ``NotADirectoryError``
because the canonical bare repository did not exist yet. These tests pin the
replacement behaviour: each missing prerequisite gets its own code and a
message that names the next step.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from app.api.errors import _from_exception
from app.claim.service import (
    ClaimGitFailed,
    ClaimGitMissing,
    ClaimRepoUnavailable,
    ClaimRequest,
    ClaimService,
)
from app.git.repo import CanonicalRepo
from tests.claim_support import make_database, seed_project, seed_task


def _claim(tmp_path: Path, repo: CanonicalRepo):
    database = make_database(tmp_path)
    seed_project(database)
    seed_task(database, task_id="T-1")
    return ClaimService(database, repo).claim(
        ClaimRequest(
            claim_request_id="cr-prereq",
            harness_id="harness-1",
            task_ids=("T-1",),
        )
    )


def test_missing_canonical_repository_names_the_repo_code(tmp_path: Path) -> None:
    repo = CanonicalRepo(tmp_path / "not-created.git")

    with pytest.raises(ClaimRepoUnavailable) as caught:
        _claim(tmp_path, repo)

    # Exact type, not a subclass: proves the OSError from subprocess never
    # escapes as itself (that was NotADirectoryError on Windows).
    assert type(caught.value) is ClaimRepoUnavailable
    assert not isinstance(caught.value, NotADirectoryError)
    assert caught.value.code == "claim.repo_unavailable"
    message = str(caught.value)
    assert "start-demo.ps1" in message
    assert "canonical" in message


def test_missing_git_executable_names_the_git_code(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = CanonicalRepo(tmp_path / "canonical.git")
    repo.ensure()
    monkeypatch.setattr("app.claim.service.shutil.which", lambda _name: None)

    with pytest.raises(ClaimGitMissing) as caught:
        _claim(tmp_path, repo)

    assert type(caught.value) is ClaimGitMissing
    assert not isinstance(caught.value, NotADirectoryError)
    assert caught.value.code == "claim.git_missing"
    message = str(caught.value)
    assert "git-scm.com" in message
    assert "PATH" in message


def test_failing_git_command_names_the_failed_code(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = CanonicalRepo(tmp_path / "canonical.git")
    repo.ensure()

    def _failing_git(*_args: object, **_kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            args=["git", "rev-parse"],
            returncode=128,
            stdout="",
            stderr="fatal: not a git repository",
        )

    monkeypatch.setattr("app.claim.service.run_git", _failing_git)

    with pytest.raises(ClaimGitFailed) as caught:
        _claim(tmp_path, repo)

    assert type(caught.value) is ClaimGitFailed
    assert not isinstance(caught.value, NotADirectoryError)
    assert caught.value.code == "claim.git_failed"
    message = str(caught.value)
    assert "rev-parse" in message
    assert "128" in message
    assert "fatal: not a git repository" in message


def test_wire_mapping_keeps_prerequisite_codes_out_of_internal_error() -> None:
    mapped = _from_exception(ClaimRepoUnavailable("canonical repository is missing"))
    assert mapped.code == "claim.repo_unavailable"
    assert mapped.http_status == 503
    assert mapped.retryable is False
    assert mapped.code != "internal.error"
