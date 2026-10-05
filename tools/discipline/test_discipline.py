# -*- coding: utf-8 -*-
"""discipline 的单测：解析纯函数 + merge 组 fail-closed 行为 + 临时 git 仓库集成。

运行（仓库根）：python -m pytest tools -q
"""
import importlib.util
import os
import shutil
import subprocess

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
    assert d.parse_merge_subject(
        "Merge branch 'main' into agent/local/x"
    ) == ["main"]
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
    checks = registry_checks(tmp_path, [
        "01-h--t1.md",
        "01-h--t2.md",
    ])
    unique_msg = next(msg for msg in checks if msg.startswith("认领文件序号唯一"))
    assert checks[unique_msg] is False
    assert "重复=['01']" in unique_msg


def test_claim_serials_must_be_contiguous(tmp_path):
    checks = registry_checks(tmp_path, [
        "01-h--t1.md",
        "03-h--t2.md",
    ])
    cont_msg = next(msg for msg in checks if msg.startswith("认领文件序号连续"))
    assert checks[cont_msg] is False
    assert "缺号=[2]" in cont_msg


def test_claim_serials_happy_path(tmp_path):
    checks = registry_checks(tmp_path, [
        "01-h--t1.md",
        "02-h--t2.md",
    ])
    assert all(checks.values())


# ---------- merge 组：注入假 lister，不依赖 git ----------

def fake_lister(branches, ok=True, detail=""):
    return lambda repo_root: (ok, branches, detail)


def run_merge(tasks, claims, cards, branches, **kw):
    result = d.Result()
    d.check_merge(
        result, ".",
        tasks=tasks, claims=claims, cards=cards,
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
    checks = run_merge(TASKS, CLAIMS, {}, [], ok=False, detail="当前目录不是 git 工作区")
    assert len(checks) == 1
    (msg, passed), = checks.items()
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
        ("git",) + args, cwd=repo, capture_output=True, text=True,
        timeout=60, check=True,
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
        ("git", "rev-parse", "HEAD"), cwd=str(repo),
        capture_output=True, text=True, check=True,
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
