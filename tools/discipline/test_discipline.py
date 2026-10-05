# -*- coding: utf-8 -*-
"""discipline 的单测：解析纯函数 + merge 组 fail-closed 行为 + 临时 git 仓库集成。

运行（仓库根）：python -m pytest tools -q
"""

import importlib.util
import io
import os
import shutil
import subprocess
from pathlib import Path

# tools/ 与 tools/discipline/ 同时在路径上时，包目录会遮蔽同名模块；
# 直接按文件路径加载，与调用方 sys.path 布局无关。
_SPEC = importlib.util.spec_from_file_location(
    "discipline_tool",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "discipline.py"),
)
d = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(d)


# ---------- 纯解析 ----------


def test_parse_merge_subject_standard_forms():
    assert d.parse_merge_subject("Merge branch 'agent/local/trae/x/a1'") == [
        "agent/local/trae/x/a1"
    ]
    assert d.parse_merge_subject(
        "Merge remote-tracking branch 'origin/agent/local/x'"
    ) == ["origin/agent/local/x"]
    assert d.parse_merge_subject("Merge branches 'a' and 'b'") == ["a", "b"]
    # 同步主线：能解析出 main，由上层过滤
    assert d.parse_merge_subject("Merge branch 'main' into agent/local/x") == ["main"]
    # 手写标题无法机械映射 —— 已知盲区，返回空而非猜测
    assert d.parse_merge_subject("Merge MVP-02 (SQLite schema) into main") == []
    assert d.parse_merge_subject("not a merge") == []


def test_normalize_branch():
    assert d.normalize_branch("`agent/local/trae/x/a1`") == "agent/local/trae/x/a1"
    assert d.normalize_branch("`main`（⚠️ 见违规记录）") == "main"
    assert d.normalize_branch("agent/local/x") == "agent/local/x"
    assert d.normalize_branch("") == ""


def test_branch_tokens_multi_branch_cell():
    # 同一任务两个尝试分支（a1 作废、a2 合入），注记里的提交号不得被当成分支
    cell = "`agent/h/t/a1` `agent/h/t/a2`（a2 结卡合入 e6b3026）"
    assert d.branch_tokens(cell) == ["agent/h/t/a1", "agent/h/t/a2"]
    assert d.branch_tokens("`agent/h/t/a1`") == ["agent/h/t/a1"]
    assert d.branch_tokens("agent/h/t/a1") == ["agent/h/t/a1"]


# ---------- 认领序号断言（2026-10-05 起序号来自 docs/claims/ 文件名） ----------

TASKS_HEADER = """## 任务表

| task_id | Harness | 依赖 | 状态 | 分支 | test_id |
|---|---|---|---|---|---|
| t1 | H | — | ✅ done | `agent/h/t1` | `MVP-PROCESS-001` |
| t2 | H | — | 🟢 doing | `agent/h/t2` | `MVP-PROCESS-001` |

## 认领登记（R10 §0 总协调规则第 2 条）
"""


def claim_file(task_id, branch=None):
    branch = branch or "agent/h/%s" % task_id
    return (
        "# 认领：%s\n\n"
        "| 字段 | 值 |\n|---|---|\n"
        "| task_id | `%s` |\n"
        "| Harness | H |\n"
        "| 认领时间 | 2026-10-05 09:00 |\n"
        "| 分支 | `%s` |\n"
        "| 状态 | ✅ done |\n"
    ) % (task_id, task_id, branch)


def registry_checks(tmp_path, claim_names):
    """claim_names：docs/claims/ 下的文件名（序号在文件名前缀）。"""
    claims_dir = tmp_path / "docs" / "claims"
    claims_dir.mkdir(parents=True)
    for name in claim_names:
        tid = name.split("--", 1)[-1][:-3]
        (claims_dir / name).write_text(claim_file(tid), encoding="utf-8")
    result = d.Result()
    d.check_registry(result, TASKS_HEADER, str(tmp_path))
    return {c["msg"]: c["ok"] for c in result.checks if "序号" in c["msg"]}


def test_claim_serials_must_be_unique(tmp_path):
    checks = registry_checks(
        tmp_path,
        [
            "01-h--t1.md",
            "01-h--t2.md",
        ],
    )
    unique_msg = next(msg for msg in checks if msg.startswith("认领文件序号唯一"))
    assert checks[unique_msg] is False
    assert "重复=['01']" in unique_msg


def test_claim_serials_must_be_contiguous(tmp_path):
    checks = registry_checks(
        tmp_path,
        [
            "01-h--t1.md",
            "03-h--t2.md",
        ],
    )
    cont_msg = next(msg for msg in checks if msg.startswith("认领文件序号连续"))
    assert checks[cont_msg] is False
    assert "缺号=[2]" in cont_msg


def test_claim_serials_happy_path(tmp_path):
    checks = registry_checks(
        tmp_path,
        [
            "01-h--t1.md",
            "02-h--t2.md",
        ],
    )
    assert all(checks.values())


# ---------- merge 组：注入假 lister，不依赖 git ----------


def fake_lister(branches, ok=True, detail=""):
    return lambda repo_root: (ok, branches, detail)


def run_merge(tasks, claims, cards, branches, **kw):
    result = d.Result()
    d.check_merge(
        result,
        ".",
        tasks=tasks,
        claims=claims,
        cards=cards,
        lister=fake_lister(branches, **kw),
    )
    return {c["msg"]: c["ok"] for c in result.checks}


TASKS = [
    {"task_id": "t1", "status": "✅ done"},
    {"task_id": "t2", "status": "🟢 doing"},
    {"task_id": "t3", "status": "✅ done"},
]
CLAIMS = [
    {"task_id": "t1", "branch": "`agent/h/t1`"},
    {"task_id": "t2", "branch": "`agent/h/t2`"},
    {"task_id": "t3", "branch": "`agent/h/t3`"},
]
GOOD_CARD = {
    "fields": {"当前状态": "done", "commit": "abc123"},
    "body": "验证结果：\n  - pytest 0\n",
}


def test_merge_fail_closed_when_git_unavailable():
    checks = run_merge(
        TASKS, CLAIMS, {}, [], ok=False, detail="当前目录不是 git 工作区"
    )
    assert len(checks) == 1
    ((msg, passed),) = checks.items()
    assert passed is False and "fail-closed" in msg


def test_merge_unregistered_branch_fails():
    checks = run_merge(TASKS, CLAIMS, {}, ["agent/h/ghost"])
    msg = next(m for m in checks if "未登记" in m)
    assert checks[msg] is False
    assert "agent/h/ghost" in msg


def test_merge_unfinished_task_fails():
    checks = run_merge(TASKS, CLAIMS, {}, ["agent/h/t2"])
    msg = next(m for m in checks if "任务表均已完成" in m)
    assert checks[msg] is False
    assert "t2" in msg


def test_merge_finished_without_card_fails():
    checks = run_merge(TASKS, CLAIMS, {}, ["agent/h/t1"])
    assert checks[next(m for m in checks if "均有任务卡" in m)] is False


def test_merge_finished_card_without_evidence_fails():
    bad_card = {"fields": {"当前状态": "done"}, "body": "什么都没留\n"}
    checks = run_merge(TASKS, CLAIMS, {"t1": bad_card}, ["agent/h/t1"])
    assert checks[next(m for m in checks if "验证证据" in m)] is False

    good_checks = run_merge(TASKS, CLAIMS, {"t1": GOOD_CARD}, ["agent/h/t1"])
    assert all(good_checks.values())


def test_merge_sync_main_branch_is_filtered_by_lister():
    # parse 层把同步合并解析成 ['main']；与 git_merged_branches 的过滤同构
    parsed = d.parse_merge_subject("Merge branch 'main' into agent/h/t1")
    assert [n for n in parsed if n != "main"] == []


# ---------- 临时 git 仓库：真实 git 集成 ----------


def git(repo, *args):
    subprocess.run(
        ("git",) + args,
        cwd=repo,
        capture_output=True,
        text=True,
        timeout=60,
        check=True,
    )


def make_commit(repo, filename, content, branch="main"):
    with open(os.path.join(repo, filename), "w", encoding="utf-8") as handle:
        handle.write(content)
    git(repo, "add", filename)
    git(repo, "commit", "-m", "%s on %s" % (filename, branch))


def test_git_lister_on_real_repository(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    git(str(repo), "init")
    git(str(repo), "symbolic-ref", "HEAD", "refs/heads/main")
    git(str(repo), "config", "user.email", "t@t")
    git(str(repo), "config", "user.name", "t")
    make_commit(str(repo), "root.txt", "base")

    # 任务分支并入主线（默认 no-ff 标题）
    git(str(repo), "checkout", "-b", "agent/h/t1")
    make_commit(str(repo), "task.txt", "work", "agent/h/t1")
    git(str(repo), "checkout", "main")
    git(str(repo), "merge", "--no-ff", "agent/h/t1")

    ok, branches, detail = d.git_merged_branches(str(repo))
    assert ok, detail
    assert branches == ["agent/h/t1"]

    # 浅克隆必须 fail-closed，即使 HEAD 恰好是合并提交。
    # 不做 file:// clone（本机临时路径可能含撇号等 URL 不安全字符）：
    # git 以 .git/shallow 文件存在与否判定浅克隆，直接构造。
    shallow = tmp_path / "shallow"
    shutil.copytree(repo, shallow)
    head = subprocess.run(
        ("git", "rev-parse", "HEAD"),
        cwd=str(repo),
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    with open(os.path.join(str(shallow), ".git", "shallow"), "w") as handle:
        handle.write(head + "\n")
    ok, _, detail = d.git_merged_branches(str(shallow))
    assert ok is False
    assert "浅克隆" in detail


def test_git_lister_excludes_sync_merge(tmp_path):
    repo = tmp_path / "repo2"
    repo.mkdir()
    git(str(repo), "init")
    git(str(repo), "symbolic-ref", "HEAD", "refs/heads/main")
    git(str(repo), "config", "user.email", "t@t")
    git(str(repo), "config", "user.name", "t")
    make_commit(str(repo), "root.txt", "base")

    git(str(repo), "checkout", "-b", "agent/h/t2")
    make_commit(str(repo), "task.txt", "task")
    git(str(repo), "checkout", "main")
    make_commit(str(repo), "main2.txt", "main moves")
    # 任务分支同步主线 → 历史里出现 Merge branch 'main' into ...
    git(str(repo), "checkout", "agent/h/t2")
    git(str(repo), "merge", "main", "--no-edit")
    git(str(repo), "checkout", "main")
    git(str(repo), "merge", "--no-ff", "agent/h/t2")

    ok, branches, detail = d.git_merged_branches(str(repo))
    assert ok, detail
    assert branches == ["agent/h/t2"]


# ---------- rootworktree 组：根工作区占用 ----------


def _root_repo(tmp_path):
    """一个「主工作区 + 一个 linked worktree」的仓库，返回主工作区路径。"""
    repo = tmp_path / "rootrepo"
    repo.mkdir()
    git(str(repo), "init")
    git(str(repo), "symbolic-ref", "HEAD", "refs/heads/main")
    git(str(repo), "config", "user.email", "t@t")
    git(str(repo), "config", "user.name", "t")
    make_commit(str(repo), "f.txt", "base")
    return repo


def test_root_worktree_clean_passes(tmp_path):
    repo = _root_repo(tmp_path)
    ok, detail = d.root_worktree_status(str(repo))
    assert ok is True
    assert "干净" in detail


def test_root_worktree_dirty_tracked_file_fails(tmp_path):
    repo = _root_repo(tmp_path)
    (repo / "f.txt").write_text("dirty", encoding="utf-8")
    ok, detail = d.root_worktree_status(str(repo))
    assert ok is False
    assert "f.txt" in detail
    assert ".worktrees" in detail, "失败信息要告诉人开发应在哪做"


def test_root_worktree_untracked_file_fails(tmp_path):
    """未跟踪文件同样算占用——新建 scripts/lib/ 这类最常见的占用形式。"""
    repo = _root_repo(tmp_path)
    (repo / "scratch").mkdir()
    (repo / "scratch" / "note.md").write_text("x", encoding="utf-8")
    ok, detail = d.root_worktree_status(str(repo))
    assert ok is False
    assert "scratch" in detail


def test_root_worktree_allows_merge_in_progress(tmp_path):
    """合并进行中必须放行——那时工作区本来就该脏，否则正常合并会被自己拦住。

    MERGE_HEAD 在 `.git/` 里，不在仓库根；写错位置会得到假阴性。
    """
    repo = _root_repo(tmp_path)
    (repo / ".git" / "MERGE_HEAD").write_text("0" * 40 + "\n", encoding="utf-8")
    (repo / "f.txt").write_text("dirty", encoding="utf-8")
    ok, detail = d.root_worktree_status(str(repo))
    assert ok is True
    assert "合并中" in detail


def test_root_worktree_checked_from_linked_worktree(tmp_path):
    """从任务 worktree 运行也必须查到**主**工作区，而不是自己脚下那个。"""
    repo = _root_repo(tmp_path)
    linked = repo / "wt"
    git(str(repo), "worktree", "add", "-b", "task", str(linked))
    (repo / "f.txt").write_text("dirty", encoding="utf-8")

    assert d.root_worktree_of(str(linked)) == os.path.normpath(str(repo))
    ok, detail = d.root_worktree_status(str(linked))
    assert ok is False, f"从 linked worktree 运行应看到主工作区脏；{detail}"


def test_root_worktree_fail_closed_without_git(tmp_path):
    """定位不到主工作区时判失败——与 merge 组一致，静默放行等于没有这道检查。"""
    plain = tmp_path / "not-a-repo"
    plain.mkdir()
    ok, detail = d.root_worktree_status(str(plain))
    assert ok is False
    assert "fail-closed" in detail


def test_check_root_worktree_records_group(tmp_path):
    """确认这条断言真的落在 rootworktree 组里，且失败会被result.failed 收集。"""
    repo = _root_repo(tmp_path)
    (repo / "f.txt").write_text("dirty", encoding="utf-8")
    result = d.Result()
    d.check_root_worktree(result, str(repo))
    assert [c["group"] for c in result.checks] == ["rootworktree"]
    assert len(result.failed) == 1
    assert "未提交改动" in result.failed[0]["msg"]


def _stub_all_groups(monkeypatch, groups):
    """把每个检查组替换成「往 result 里塞一条指定 group 的通过项」。

    这样测的是 run() 里的**豁免逻辑**，而不是各组自己的业务断言。

    注意替换函数必须先建好再赋值——若在替换函数体内再调用同名工厂，
    会无限递归（第一版就踩了这个）。
    """

    def make(name, group):
        def _inner(result, repo_root, *a, **k):
            result.chk(True, "%s ok" % name, group)
            if name == "check_registry":
                return [], []
            if name == "check_cards":
                return {}
            return None

        return _inner

    for name, group in groups.items():
        monkeypatch.setattr(d, name, make(name, group))


_GROUPS = {
    "check_registry": "registry",
    "check_cards": "cards",
    "check_counts": "counts",
    "check_shared_file_ownership": "ownership",
    "check_dependencies": "deps",
    "check_traceability": "traceability",
    "check_merge": "merge",
    "check_branch_visibility": "branches",
    "check_root_worktree": "rootworktree",
}


def test_skip_group_removes_only_that_group(tmp_path, monkeypatch):
    """豁免必须精确：只少被豁免的那一组，其他组一条不少。"""
    _stub_all_groups(monkeypatch, _GROUPS)
    io.open(tmp_path / "TASKS.md", "w", encoding="utf-8").write("x")

    full = d.run(str(tmp_path))
    assert "rootworktree" in {c["group"] for c in full.checks}
    assert len(full.skipped) == 0

    skipped = d.run(str(tmp_path), skip_groups=["rootworktree"])
    groups = {c["group"] for c in skipped.checks}
    assert "rootworktree" not in groups
    assert {"merge", "branches"} <= groups, "豁免不能波及其他组"
    assert skipped.skipped == ["rootworktree"], "豁免必须留痕"
    assert len(skipped.checks) == len(full.checks) - 1


def test_skip_group_unknown_name_is_harmless(tmp_path, monkeypatch):
    """豁免一个不存在的组名不应报错，也不应少跑任何断言——避免手滑就静默放过。"""
    _stub_all_groups(monkeypatch, _GROUPS)
    io.open(tmp_path / "TASKS.md", "w", encoding="utf-8").write("x")

    full = d.run(str(tmp_path))
    typo = d.run(str(tmp_path), skip_groups=["rootworktree_typo"])
    assert len(typo.checks) == len(full.checks)
    assert typo.skipped == []


def test_mergeguard_exempts_rootworktree_gate():
    """mergeguard 合并时必须豁免 rootworktree，且豁免要在输出里可见。

    不豁免会死锁：合并期间根工作区本就该是「合并中」的脏状态，那条断言必然
    变红；而占用者不清干净就合不进去，于是谁也动不了。
    """
    cli = Path(d.__file__).with_name("mergeguard") / "cli.py"
    if not cli.exists():  # discipline 的测试可能在只装了 discipline 的环境跑
        return
    text = io.open(cli, encoding="utf-8").read()
    assert "--skip-group" in text
    assert "rootworktree" in text
    # 豁免信息必须被打出来，不能只存在于代码里
    assert "[skip]" in text or "ln.startswith" in text
