"""Actual git result checks: scope escapes and dirty outputs cannot publish."""

from pathlib import Path

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
    assert result.returncode == 1 and "out-of-scope" in result.stderr


def test_structural_gate_requires_nonempty_allowed_artifact(tmp_path: Path):
    assert not verify(tmp_path, ["docs/output.md"])
    (tmp_path / "docs").mkdir()
    output = tmp_path / "docs/output.md"
    output.write_text("", encoding="utf-8")
    assert not verify(tmp_path, ["docs/output.md"])
    output.write_text("Human review still required", encoding="utf-8")
    assert verify(tmp_path, ["docs/output.md"])
