"""集成检查的文档档：canonical 仓库只有文档时，pytest 会以退出码 5 变红。

`docs_profile.check` 是那份检查的全部逻辑，这里逐条钉住它的判定，避免
以后有人把"通过"改成无条件通过。
"""

from __future__ import annotations

from app.verify.docs_profile import check, main


def _write(root, name: str, text: str) -> None:
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_a_docs_repository_passes(tmp_path):
    _write(tmp_path, "README.md", "# canonical\n")
    _write(tmp_path, "docs/design.md", "# design\n")

    assert check(tmp_path) == []
    assert main(["docs_profile.py", str(tmp_path)]) == 0


def test_missing_readme_is_a_failure(tmp_path):
    _write(tmp_path, "docs/design.md", "# design\n")

    assert "README.md is missing" in check(tmp_path)


def test_an_empty_markdown_file_is_a_failure(tmp_path):
    _write(tmp_path, "README.md", "# canonical\n")
    _write(tmp_path, "docs/empty.md", "\n  \n")

    assert any("docs/empty.md is empty" in problem for problem in check(tmp_path))


def test_invalid_utf8_is_a_failure(tmp_path):
    _write(tmp_path, "README.md", "# canonical\n")
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "broken.md").write_bytes(b"\xff\xfe\x00")

    assert any("broken.md is not valid UTF-8" in problem for problem in check(tmp_path))


def test_a_repository_without_markdown_is_a_failure(tmp_path):
    (tmp_path / "notes.txt").write_text("nothing to verify\n", encoding="utf-8")

    problems = check(tmp_path)
    assert "no Markdown files in the repository" in problems


def test_git_internals_are_ignored(tmp_path):
    _write(tmp_path, "README.md", "# canonical\n")
    _write(tmp_path, ".git/COMMIT_EDITMSG.md", "\n")

    assert check(tmp_path) == []
