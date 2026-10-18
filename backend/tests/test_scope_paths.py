"""Regression tests for the shared ``exclusive_paths`` scope rule.

The formal dispatch API accepts a bare directory as an exclusive path, so the
worker and the artifact verifier must agree that ``src`` authorizes
``src/note.md``.  ``fnmatchcase`` alone says it does not, which made every
directory-scoped task unsubmittable.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from app.adapter import verify_artifact
from app.adapter.scope import path_in_scope

BACKEND_ROOT = Path(__file__).resolve().parents[1]


def test_directory_pattern_authorizes_files_below_it():
    assert path_in_scope("src/FD-1cfbd3f5e9c8ad29.md", ["src"]) is True
    assert path_in_scope("src/nested/deep.md", ["src"]) is True
    assert path_in_scope("src", ["src"]) is True


def test_directory_pattern_does_not_leak_to_sibling_prefixes():
    assert path_in_scope("src2/note.md", ["src"]) is False
    assert path_in_scope("src-notes.md", ["src"]) is False


def test_exact_file_and_glob_patterns_still_work():
    assert path_in_scope("docs/x/note.md", ["docs/x/note.md"]) is True
    assert path_in_scope("docs/x/other.md", ["docs/x/note.md"]) is False
    assert path_in_scope("src/note.md", ["src/**"]) is True
    assert path_in_scope("notes/a.md", ["*.md"]) is True
    assert path_in_scope("notes/a.txt", ["*.md"]) is False


def test_out_of_scope_and_blank_patterns_are_refused():
    assert path_in_scope("docs/other.md", ["src"]) is False
    assert path_in_scope("src/note.md", []) is False
    assert path_in_scope("src/note.md", ["", "   "]) is False
    assert path_in_scope("", ["src"]) is False


def test_windows_separators_are_normalized():
    assert path_in_scope("src\\note.md", ["src"]) is True


def test_verify_artifact_accepts_a_directory_pattern(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "note.md").write_text("deliverable\n", encoding="utf-8")
    assert verify_artifact.verify(tmp_path, ["src"]) is True
    assert verify_artifact.verify(tmp_path, ["docs"]) is False


def test_standalone_verify_artifact_script_honours_a_directory_pattern(tmp_path):
    """The profile runs this file as a script; the rule must survive that path."""
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "note.md").write_text("deliverable\n", encoding="utf-8")
    script = BACKEND_ROOT / "app" / "adapter" / "verify_artifact.py"

    ok = subprocess.run(
        [sys.executable, str(script), json.dumps(["src"])],
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )
    assert ok.returncode == 0, ok.stderr
    assert json.loads(ok.stdout)["nonempty_scoped_artifact"] is True

    refused = subprocess.run(
        [sys.executable, str(script), json.dumps(["docs"])],
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )
    assert refused.returncode == 1
