"""Actual git result checks: scope escapes and dirty outputs cannot publish."""

from pathlib import Path

import pytest

from app.adapter.formal_worker import ScopedRunner
from app.adapter.verify_artifact import verify
from app.git.repo import run_git


def test_scoped_runner_rejects_committed_outside_path(tmp_path):
    run_git("init", str(tmp_path))
    run_git("commit", "--allow-empty", "-m", "seed", cwd=tmp_path)
    base = run_git("rev-parse", "HEAD", cwd=tmp_path).stdout.strip()
    (tmp_path / "outside.txt").write_text("escape", encoding="utf-8")
    run_git("add", "outside.txt", cwd=tmp_path)
    run_git("commit", "-m", "outside", cwd=tmp_path)
    runner = ScopedRunner({"exclusive_paths": ["docs/allowed.md"]}, base)
    import sys

    result = runner.run([sys.executable, "-c", "pass"], cwd=tmp_path, timeout_seconds=10)
    # The refusal text is the operator-facing message for a scope escape; it
    # changed from "out-of-scope" to "out of scope" when the gate started
    # accepting files below a scoped directory (app/adapter/scope.py).
    assert result.returncode == 1 and "out of scope" in result.stderr


def test_structural_gate_requires_nonempty_allowed_artifact(tmp_path: Path):
    assert not verify(tmp_path, ["docs/output.md"])
    (tmp_path / "docs").mkdir()
    output = tmp_path / "docs/output.md"
    output.write_text("", encoding="utf-8")
    assert not verify(tmp_path, ["docs/output.md"])
    output.write_text("Human review still required", encoding="utf-8")
    assert verify(tmp_path, ["docs/output.md"])


def test_platform_commit_commits_only_the_exclusive_path(tmp_path: Path):
    run_git("init", str(tmp_path))
    run_git("commit", "--allow-empty", "-m", "seed", cwd=tmp_path)
    base = run_git("rev-parse", "HEAD", cwd=tmp_path).stdout.strip()
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "allowed.md").write_text("submitted", encoding="utf-8")
    runner = ScopedRunner({"exclusive_paths": ["docs/allowed.md"]}, base, platform_commit=True)
    import sys

    result = runner.run([sys.executable, "-c", "pass"], cwd=tmp_path, timeout_seconds=10)
    assert result.returncode == 0
    assert run_git("status", "--porcelain", cwd=tmp_path).stdout == ""
    assert (
        run_git("diff", "--name-only", f"{base}..HEAD", cwd=tmp_path).stdout.strip()
        == "docs/allowed.md"
    )


def test_platform_commit_rejects_an_out_of_scope_dirty_path(tmp_path: Path):
    run_git("init", str(tmp_path))
    run_git("commit", "--allow-empty", "-m", "seed", cwd=tmp_path)
    base = run_git("rev-parse", "HEAD", cwd=tmp_path).stdout.strip()
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "allowed.md").write_text("submitted", encoding="utf-8")
    (tmp_path / "outside.md").write_text("escape", encoding="utf-8")
    runner = ScopedRunner({"exclusive_paths": ["docs/allowed.md"]}, base, platform_commit=True)
    import sys

    with pytest.raises(RuntimeError, match="outside exclusive_paths"):
        runner.run([sys.executable, "-c", "pass"], cwd=tmp_path, timeout_seconds=10)
    assert run_git("rev-parse", "HEAD", cwd=tmp_path).stdout.strip() == base


def test_platform_commit_rejects_empty_result(tmp_path: Path):
    run_git("init", str(tmp_path))
    run_git("commit", "--allow-empty", "-m", "seed", cwd=tmp_path)
    base = run_git("rev-parse", "HEAD", cwd=tmp_path).stdout.strip()
    runner = ScopedRunner({"exclusive_paths": ["docs/allowed.md"]}, base, platform_commit=True)
    import sys

    result = runner.run([sys.executable, "-c", "pass"], cwd=tmp_path, timeout_seconds=10)
    assert result.returncode == 1
    # "nothing committed" and "committed outside the scope" are distinct
    # refusals now; this test is about the empty one.
    assert "no committed change" in result.stderr
