"""F1：契约一致性提示 + 运行产物不算越界。

e16 现场的四个"越界"路径全是新建：``static/index.html`` 是真实产出（契约没写），
另外三条 ``.pyc`` 只是跑代码留下的运行产物。判定代码本身没错，错的是把两者
放在同一类里报，导致真正要找的问题（契约与 goal 自相矛盾）被噪声盖住。

这里钉住：

1. goal 提到 ``tests/conftest.py``（"via X" 句式）而契约没写 → 批准时给出警告；
2. goal 提到的路径都在契约里 → 不打扰人；
3. ``.pyc`` / ``__pycache__/`` / ``*.log`` 落盘 → 不判越界、不进 commit、记一条信息；
4. ``static/index.html`` 仍然走越界/警告路径，不被运行产物白名单放过。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from app.adapter.formal_worker import OutOfScopeSubmission, ScopedRunner
from app.adapter.scope import is_runtime_artifact
from app.git.repo import run_git
from app.plan.consistency import extract_mentioned_paths, goal_scope_warnings


def _seed(tmp_path: Path) -> str:
    run_git("init", str(tmp_path))
    run_git("commit", "--allow-empty", "-m", "seed", cwd=tmp_path)
    return run_git("rev-parse", "HEAD", cwd=tmp_path).stdout.strip()


# ------------------------------------------------------------ goal 一致性


def test_goal_mentioning_a_path_outside_the_contract_is_flagged():
    """e16 原句：``e.g., via conftest.py or autouse fixture``。"""
    warnings = goal_scope_warnings(
        [
            {
                "id": "T-1",
                "goal": "Add an autouse fixture so tests share one state store;"
                " e.g., via tests/conftest.py or an autouse fixture.",
                "exclusive_paths": ["tests/test_state.py"],
            }
        ]
    )

    assert warnings
    assert any("tests/conftest.py" in line for line in warnings)
    assert "exclusive_paths" in warnings[0]


def test_bare_file_name_is_matched_by_the_full_path_in_the_contract():
    """goal 写裸名 ``conftest.py``，契约写 ``tests/conftest.py``：同一件事，不报。"""
    assert goal_scope_warnings(
        [
            {
                "id": "T-1",
                "goal": "Register the fixture via conftest.py",
                "exclusive_paths": ["tests/conftest.py"],
            }
        ]
    ) == []


def test_no_warning_when_every_mentioned_path_is_in_scope():
    assert goal_scope_warnings(
        [
            {
                "id": "T-1",
                "goal": "在 tests/conftest.py 里加 fixture，并修改 docs/guide.md",
                "exclusive_paths": ["tests/conftest.py", "docs/guide.md"],
            }
        ]
    ) == []


def test_extraction_covers_via_locative_and_modify_phrasings():
    text = (
        "Use the shared store via tests/conftest.py. "
        "在 docs/ 里补一段说明，修改 app/api/routes.py。"
    )
    found = extract_mentioned_paths(text)

    assert "tests/conftest.py" in found
    assert "docs" in found
    assert "app/api/routes.py" in found


def test_no_extraction_without_an_action_context():
    """没有动作语境的提及不是"要动它"，宁可漏报也不误报。"""
    assert extract_mentioned_paths("The output looked like index.html.") == []


# ------------------------------------------------------------ 运行产物


def test_pyc_files_are_not_judged_out_of_scope_and_are_not_committed(tmp_path):
    base = _seed(tmp_path)
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "allowed.md").write_text("submitted", encoding="utf-8")
    cache = tmp_path / "docs" / "__pycache__"
    cache.mkdir()
    (cache / "allowed.cpython-313.pyc").write_bytes(b"\x00")
    (tmp_path / "run.log").write_text("noise", encoding="utf-8")

    runner = ScopedRunner({"exclusive_paths": ["docs/allowed.md"]}, base, platform_commit=True)
    result = runner.run([sys.executable, "-c", "pass"], cwd=tmp_path, timeout_seconds=10)

    assert result.returncode == 0, result.stderr
    notes = " ".join(result.notes)
    assert "runtime artifact skipped" in notes
    assert "run.log" in notes
    # 真实交付进了 commit，运行产物没有。
    assert (
        run_git("diff", "--name-only", f"{base}..HEAD", cwd=tmp_path).stdout.strip()
        == "docs/allowed.md"
    )
    tracked = run_git("ls-files", cwd=tmp_path).stdout.splitlines()
    assert "docs/allowed.md" in tracked
    assert not any(path.endswith(".pyc") for path in tracked)
    assert "run.log" not in tracked


def test_a_runtime_artifact_already_staged_by_the_adapter_is_dropped(tmp_path):
    """适配器自己 git add 了 .pyc：提交侧把它从索引里撤掉，不改判越界。"""
    base = _seed(tmp_path)
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "allowed.md").write_text("submitted", encoding="utf-8")
    cache = tmp_path / "docs" / "__pycache__"
    cache.mkdir()
    artifact = cache / "allowed.cpython-313.pyc"
    artifact.write_bytes(b"\x00")
    run_git("add", "docs/__pycache__/allowed.cpython-313.pyc", cwd=tmp_path)

    runner = ScopedRunner({"exclusive_paths": ["docs/allowed.md"]}, base, platform_commit=True)
    result = runner.run([sys.executable, "-c", "pass"], cwd=tmp_path, timeout_seconds=10)

    assert result.returncode == 0, result.stderr
    tracked = run_git("ls-files", cwd=tmp_path).stdout.splitlines()
    assert not any(path.endswith(".pyc") for path in tracked)
    assert "docs/allowed.md" in tracked


def test_a_real_deliverable_is_still_out_of_scope(tmp_path):
    """``static/index.html`` 是真实产出，不能被运行产物白名单一起放过。"""
    base = _seed(tmp_path)
    (tmp_path / "static").mkdir()
    (tmp_path / "static" / "index.html").write_text("<html></html>", encoding="utf-8")
    (tmp_path / "run.log").write_text("noise", encoding="utf-8")

    runner = ScopedRunner({"exclusive_paths": ["src/main.py"]}, base, platform_commit=True)
    with pytest.raises(OutOfScopeSubmission) as info:
        runner.run([sys.executable, "-c", "pass"], cwd=tmp_path, timeout_seconds=10)

    assert info.value.paths == ["static/index.html"]
    assert not is_runtime_artifact("static/index.html")
