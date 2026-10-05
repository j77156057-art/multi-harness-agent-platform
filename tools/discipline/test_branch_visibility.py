# -*- coding: utf-8 -*-
"""branch-visibility 组的单测。

git 调用通过 `lister` / `out` 两个注入点替换，**不monkeypatch subprocess**
（早先直接替换 subprocess 模块，污染了同目录的 test_discipline.py）。
"""
import importlib.util
import os

_SPEC = importlib.util.spec_from_file_location(
    "discipline_tool_bv",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "discipline.py"),
)
d = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(d)


def _tasks():
    return [
        {"task_id": "MVP-07", "status": "⏳ waiting"},
        {"task_id": "MVP-06", "status": "🟢 doing"},
    ]


def _run(branches, ahead="1", claim_files=("docs/claims/30-qoder--MVP-07.md",)):
    def fake_out(repo_root, *args):
        if args[0] == "rev-list":
            return ahead + "\n"
        if args[0] == "ls-tree":
            return "".join(name + "\n" for name in claim_files)
        raise AssertionError("unexpected git call: %s" % (args,))

    result = d.Result()
    d.check_branch_visibility(
        result,
        ".",
        _tasks(),
        lister=lambda root: (True, branches, ""),
        out=fake_out,
    )
    return result


def _failed(result):
    return [c["msg"] for c in result.failed if c["group"] == "branches"]


# ---------- 应该变红 ----------

def test_unregistered_wip_branch_fails():
    """开工中的分支既没登记到 main、也没登记到自己分支 → 变红。"""
    messages = _failed(_run(["agent/local/x/MVP-07/a1"], claim_files=()))
    assert messages and "未登记" in messages[0], messages


def test_three_segment_legacy_branch_name_detected():
    """早期命名 `agent/<harness>/<task-id>`（3 段）也要能识别。"""
    assert _failed(_run(["agent/workbuddy/MVP-07"], claim_files=()))


def test_five_segment_standard_branch_name_detected():
    """标准命名 `agent/<group>/<harness>/<task-id>/<attempt>`（5 段）。"""
    assert _failed(_run(["agent/local/x/MVP-07/a1"], claim_files=()))


# ---------- 不该变红 ----------

def test_registered_on_own_branch_does_not_fail():
    """已在自己分支登记、只是还没合入 main → 不变红（正确流程的中间态）。"""
    assert _failed(_run(["agent/local/x/MVP-07/a1"])) == []


def test_pending_registration_is_still_reported():
    """不报红，但要让复核者看得见有待合入的登记。"""
    result = _run(["agent/local/x/MVP-07/a1"])
    notes = [
        c["msg"] for c in result.checks
        if c["group"] == "branches" and "待合入" in c["msg"]
    ]
    assert notes and "MVP-07" in notes[0], notes


def test_started_task_is_out_of_scope():
    """任务已开工（doing）→ 本组不适用，merge 组管。"""
    assert _failed(_run(["agent/local/x/MVP-06/a1"], claim_files=())) == []


def test_branch_without_commits_is_ignored():
    """分支没有领先 main 的提交 → 不是在途工作。"""
    assert _failed(_run(["agent/local/x/MVP-07/a1"], ahead="0", claim_files=())) == []


def test_resident_workspace_branches_skipped():
    """驻留分支不是任务分支。"""
    assert _failed(_run(["agent/workspace/claude"], claim_files=())) == []


def test_unknown_branch_shapes_skipped_quietly():
    """路径段里找不到已知 task_id → 静默跳过，不误报。

    （曾按固定下标取 task_id，对 40 个分支误报，整条检查因此不可用。）
    """
    assert _failed(_run(["feature/random-thing", "main"], claim_files=())) == []


# ---------- fail-closed ----------

def test_lister_failure_fails_closed():
    """git 不可用 → 报红，不静默放行。"""
    result = d.Result()
    d.check_branch_visibility(
        result, ".", _tasks(), lister=lambda root: (False, [], "不是 git 工作区")
    )
    assert _failed(result), "git 环境不可用时必须 fail-closed"


# ---------- 真实仓库（只读） ----------

def test_git_local_branches_on_real_repo():
    ok, branches, detail = d.git_local_branches(".")
    assert ok, detail
    assert "main" in branches
    assert all(branch and " " not in branch for branch in branches)


def test_git_local_branches_outside_repo_fails_closed(tmp_path):
    ok, _branches, _detail = d.git_local_branches(str(tmp_path))
    assert not ok, "非 git 目录必须 fail-closed"


def test_git_out_returns_none_on_failure(tmp_path):
    assert d.git_out(str(tmp_path), "rev-parse", "--is-inside-work-tree") is None
