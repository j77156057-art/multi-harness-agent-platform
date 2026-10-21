"""非 ASCII 路径曾让"写在范围内的文件"被判成越界。

实测：管理员把目标细化成了带中文目录的任务（``docs/prep/自循环验收-1020/...``），
成员 CLI **只**写了被允许的那一个文件，平台却拒绝提交：

    RuntimeError: platform commit refused: dirty path outside exclusive_paths

原因不在成员：``git status --porcelain`` 默认把非 ASCII 路径**加引号并转义**
（``"docs/prep/\\350\\207\\250..."``），带空格的路径也会加引号。范围检查拿这个
转义后的字符串去比对授权路径，自然对不上。

修法：平台每次调 git 都带上 ``core.quotepath=false``（``app.git.repo.run_git``），
并且在范围检查前把仍然带引号的路径还原（``formal_worker._unquote_git_path``）。
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from app.adapter.formal_worker import _unquote_git_path
from app.adapter.scope import path_in_scope
from app.git.repo import run_git

CHINESE = "docs/prep/自循环验收-1020/source-inventory.md"
SPACED = "docs/with space.md"


def _repo(tmp_path: Path) -> Path:
    work = tmp_path / "work"
    work.mkdir()
    run_git("init", "-q", str(work), cwd=work)
    run_git("config", "user.email", "a@b.c", cwd=work)
    run_git("config", "user.name", "t", cwd=work)
    (work / "README.md").write_text("seed\n", encoding="utf-8")
    (work / "docs").mkdir()
    (work / "docs" / ".keep").write_text("", encoding="utf-8")
    run_git("add", "-A", cwd=work)
    run_git("commit", "-qm", "seed", cwd=work)
    return work


def test_git_returns_raw_paths_for_non_ascii_and_spaces(tmp_path):
    """平台自己的 git 调用必须拿到原始路径（这是修好的前提）。

    真实场景：成员在一个**新的**中文目录下写文件，``git status --porcelain``
    报的是那个目录（``?? docs/prep/自循环验收-1020/``），平台再���
    ``ls-files --others`` 把它展开成文件。两条都得是原始路径。
    """
    work = _repo(tmp_path)
    (work / "docs" / "prep" / "自循环验收-1020").mkdir(parents=True)
    (work / "docs" / "prep" / "自循环验收-1020" / "source-inventory.md").write_text(
        "x\n", encoding="utf-8"
    )
    (work / "docs").mkdir(exist_ok=True)
    (work / "docs" / "with space.md").write_text("y\n", encoding="utf-8")

    status = run_git("status", "--porcelain", cwd=work).stdout
    others = run_git("ls-files", "--others", "--exclude-standard", cwd=work).stdout.splitlines()

    # git 把未跟踪的目录折叠成 `?? docs/prep/`，平台看到结尾是 "/" 就用
    # `ls-files --others` 展开成文件——那一步必须拿到原始路径。
    assert "?? docs/prep/" in status, status
    assert CHINESE in others, others
    assert SPACED in others, others


def test_a_quoted_spaced_path_is_still_quoted_by_git(tmp_path):
    """``core.quotepath=false`` 只管非 ASCII；带空格的路径 git 照样加引号。

    所以范围检查前面必须把引号还原——这就是 ``_unquote_git_path`` 存在的原因。
    """
    work = _repo(tmp_path)
    (work / "docs" / "with space.md").write_text("y\n", encoding="utf-8")

    status = run_git("status", "--porcelain", cwd=work).stdout

    assert '"docs/with space.md"' in status, status
    assert _unquote_git_path('"docs/with space.md"') == "docs/with space.md"


def test_git_returns_raw_paths_for_tracked_non_ascii_files(tmp_path):
    """已跟踪的非 ASCII 文件（改动后）也必须是原始路径。"""
    work = _repo(tmp_path)
    target = work / "docs" / "prep" / "自循环验收-1020" / "source-inventory.md"
    target.parent.mkdir(parents=True)
    target.write_text("x\n", encoding="utf-8")
    run_git("add", "-A", cwd=work)
    run_git("commit", "-qm", "add", cwd=work)
    target.write_text("y\n", encoding="utf-8")

    status = run_git("status", "--porcelain", cwd=work).stdout
    changed = run_git("diff", "--name-only", cwd=work).stdout.splitlines()

    assert CHINESE in status, status
    assert CHINESE in changed, changed


def test_a_quoted_path_is_unwrapped():
    assert _unquote_git_path('"docs/with space.md"') == "docs/with space.md"
    # 自循环 = E8 87 AA E5 BE AA E7 8E AF, 本 = E6 9C AC
    assert (
        _unquote_git_path(
            '"docs/prep/\\350\\207\\252\\345\\276\\252\\347\\216\\257/\\346\\234\\254.md"'
        )
        == "docs/prep/自循环/本.md"
    )
    # A plain path is returned untouched.
    assert _unquote_git_path("docs/plain.md") == "docs/plain.md"


def test_the_scope_check_accepts_a_non_ascii_authorized_path():
    """The authorized path and the git path must compare equal."""
    assert path_in_scope(CHINESE, [CHINESE]) is True
    assert path_in_scope(SPACED, [SPACED]) is True
    assert path_in_scope("docs/prep/别的/file.md", [CHINESE]) is False
