# -*- coding: utf-8 -*-
"""discipline 的单测：解析纯函数 + merge 组 fail-closed 行为 + 临时 git 仓库集成。

运行（仓库根）：python -m pytest tools -q
"""

import importlib.util
import io
import json
import os
import shutil
import subprocess
import time
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


def test_gap_in_serials_is_accepted(tmp_path):
    """跳号不再报错 —— 它是无害的，且并发分支下无法避免。

    原「序号连续 1..N」断言由 `claim-seq-adopt` 按
    `docs/proposals/claim-seq-contiguity.md` 的裁决撤销：连续性在并发下
    **结构上不可满足**（各 agent 从 main 最大值往后取号，合并顺序不由取号者决定），
    而它没有防住任何唯一性没防住的东西。
    """
    checks = registry_checks(tmp_path, [
        "01-h--t1.md",
        "03-h--t2.md",
    ])
    assert all(checks.values()), checks


def test_claim_filename_must_match_its_content(tmp_path):
    """文件名与内容必须指向同一个 task —— 这是替代连续性的那条不变量。

    它能抓真实错漏（文件被改名、放错任务、复制后忘改内容），
    且不依赖任何跨分支的共享计数器，因此两个 agent 独立操作也能同时满足。
    """
    claims_dir = tmp_path / "docs" / "claims"
    claims_dir.mkdir(parents=True)
    (claims_dir / "01-h--t1.md").write_text(claim_file("t2"), encoding="utf-8")

    result = d.Result()
    d.check_registry(result, TASKS_HEADER, str(tmp_path))
    message = next(c["msg"] for c in result.checks if "文件名" in c["msg"])
    assert "t2" in message and "01-h--t1.md" in message
    assert next(c for c in result.checks if "文件名" in c["msg"])["ok"] is False


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


def test_root_worktree_red_only_for_the_occupier(tmp_path, monkeypatch):
    """**C 方案的核心**：同一个占用事实，只让占用者本人拿到红灯。

    早先版本一视同仁：根工作区脏 → 所有人的 `check` 全红，包括正在自己
    worktree 干活、跟这事毫无关系的人。今天这个代价实际发生：它堵了十几次
    合并，其中几次是我自己的。**代价落在所有人身上，却只有一个人是原因**
    —— 那是错配。

    现在：调用方就在根工作区（即占用者）→ 判红；调用方在别的 worktree →
    记告警，不判红、不阻塞。
    """
    repo = _root_repo(tmp_path)
    linked = repo / "wt"
    git(str(repo), "worktree", "add", "-b", "task", str(linked))
    (repo / "f.txt").write_text("dirty", encoding="utf-8")

    occupier = d.Result()
    d.check_root_worktree(occupier, str(repo))
    assert len(occupier.failed) == 1, "占用者本人必须拿到红灯"
    assert not occupier.warnings

    bystander = d.Result()
    d.check_root_worktree(bystander, str(linked))
    assert not bystander.failed, "别人的 worktree 不该被这件事阻塞"
    assert len(bystander.warnings) == 1, "但必须有一条可见告警"
    msg = bystander.warnings[0]["msg"]
    assert "不判红" in msg
    assert "f.txt" in msg, "告警必须点名具体文件"


def test_root_worktree_bystander_still_sees_a_failure_when_unlocatable(
    tmp_path, monkeypatch
):
    """**fail-closed 不因作用域反转而放松**：定位不到主工作区时对谁都判红。

    这条容易在「让告警更友好」时被顺手削掉 —— 但无法判断状态时静默放行，
    等于没有这道检查。作用域反转只针对「知道是占用、且占用者不是自己」。
    """
    result = d.Result()
    monkeypatch.setattr(d, "root_worktree_of", lambda _r: None)
    d.check_root_worktree(result, str(tmp_path))
    assert len(result.failed) == 1
    assert "fail-closed" in result.failed[0]["msg"]
    assert not result.warnings


def test_warn_is_not_a_silent_pass(tmp_path):
    """告警**默认就打印**，且进 JSON 的 stdout 体 —— 不写成 stderr。

    两点都来自 TRAE 评审问题 3 的教训：`{"skipped":...}` 只写 stderr，而
    `gate.yml` 的 `--json` 重定向只收 stdout，消费方根本看不到。
    另外告警不能只在 `--verbose` 下出现 —— 它的全部意义就是「不该被静默」。
    """
    import subprocess
    import sys

    probe = (
        "import io,sys;"
        "sys.path.insert(0, %r);"
        "import discipline as d;"
        "r=d.Result();"
        "r.warn('W', 'g');"
        "assert len(r.warnings)==1, r.checks;"
        "assert len(r.passed)==0, 'warn 不该算通过项';"
        "assert r.failed==[];"
        "print('OK')" % str(Path(d.__file__).parent)
    )
    proc = subprocess.run(
        [sys.executable, "-c", probe], capture_output=True, text=True
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "OK" in proc.stdout


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


def _take_merge_lock(repo, pid=None):
    """在临时仓库里造一把**活着的**合并锁，模拟受控合并进行中。"""
    lock = repo / ".git" / "merge-lock"
    lock.mkdir(parents=True, exist_ok=True)
    (lock / "owner.json").write_text(
        json.dumps(
            {
                "pid": os.getpid() if pid is None else pid,
                "task_id": "T-merge",
                "branch": "agent/local/x/T-merge/a1",
                "worktree": str(repo),
                "acquired_at": time.time(),
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return lock


def test_root_worktree_allows_controlled_merge_in_progress(tmp_path):
    """受控合并进行中必须放行——那时工作区本来就该脏，否则正常合并被自己拦住。

    判据是「无未合并条目 + 合并锁持有者存活」，不再是一句「MERGE_HEAD 存在」。
    MERGE_HEAD 在 `.git/` 里，不在仓库根；写错位置会得到假阴性。
    """
    repo = _root_repo(tmp_path)
    (repo / ".git" / "MERGE_HEAD").write_text("0" * 40 + "\n", encoding="utf-8")
    _take_merge_lock(repo)
    (repo / "f.txt").write_text("dirty", encoding="utf-8")
    ok, detail = d.root_worktree_status(str(repo))
    assert ok is True, detail
    assert "合并中" in detail
    assert "存活" in detail, "必须说明凭什么放行"


def test_root_worktree_bare_merge_head_does_not_exempt(tmp_path):
    """**回归**：只有 MERGE_HEAD 而无合并锁，必须判红。

    这是 TRAE 评审（root-worktree-clean-review 问题 1）打穿的漏洞，也是本组
    卡里声明的头号场景：别人在根工作区写代码、留下合并现场 —— 那种形态
    恰好满足「MERGE_HEAD 存在」，于是本组在自己声明要防的场景里判 ok=True。
    子 agent 更实证：**手工写一个 40 字符的 `.git/MERGE_HEAD` 就能让占用
    状态静音。** 判据已从「存在即放行」收紧为「存在且锁持有者存活」。
    """
    repo = _root_repo(tmp_path)
    (repo / ".git" / "MERGE_HEAD").write_text("0" * 40 + "\n", encoding="utf-8")
    (repo / "f.txt").write_text("dirty", encoding="utf-8")
    ok, detail = d.root_worktree_status(str(repo))
    assert ok is False, detail
    assert "非受控合并" in detail or "没有合并锁" in detail


def test_root_worktree_dead_merge_lock_holder_does_not_exempt(tmp_path):
    """合并锁持有者进程已退出 -> 属崩溃残留，不放行。

    pid 999999 在实践中不存在（Linux pid 上限约 4194304，Windows 默认更小）。
    """
    repo = _root_repo(tmp_path)
    (repo / ".git" / "MERGE_HEAD").write_text("0" * 40 + "\n", encoding="utf-8")
    _take_merge_lock(repo, pid=999999)
    (repo / "f.txt").write_text("dirty", encoding="utf-8")
    ok, detail = d.root_worktree_status(str(repo))
    assert ok is False, detail
    assert "已退出" in detail or "存活" not in detail


def test_root_worktree_unresolved_conflicts_never_exempt(tmp_path):
    """**回归**：有未合并条目（UU/AA/DD…）时，即便锁活着也绝不放行。

    「别人留下未解决冲突」正是本组要防的头号场景，而它在 git 里表现为
    MERGE_HEAD 存在 **且** status 有未合并条目 —— 只查锁会把这一形态放过。
    两条判据必须同时成立，缺一不可。
    """
    repo = _root_repo(tmp_path)
    git(repo, "checkout", "-b", "side")
    (repo / "c.txt").write_text("side", encoding="utf-8")
    git(repo, "add", "c.txt")
    git(repo, "commit", "-m", "side")
    git(repo, "checkout", "main")
    (repo / "c.txt").write_text("main", encoding="utf-8")
    git(repo, "add", "c.txt")
    git(repo, "commit", "-m", "main")
    # 起一个真冲突，留下未解决状态
    merge = subprocess.run(
        ["git", "-C", str(repo), "merge", "side"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    assert merge.returncode != 0, "本用例需要一个真冲突"
    assert (repo / ".git" / "MERGE_HEAD").exists()
    _take_merge_lock(repo)  # 锁是活的

    ok, detail = d.root_worktree_status(str(repo))
    assert ok is False, "有未合并条目时不得放行：" + detail
    assert "未合并" in detail


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

def test_no_skip_group_backdoor_remains():
    """**回归**：豁免机制已整体回退，CLI 上不应再有能关掉检查的开关。

    TRAE 评审问题 2/3：`--skip-group` 不解决任何问题（父提交的豁免已覆盖），
    却是**通用后门** —— 无白名单、无理由、无鉴权，实测对全部组重复传参可得
    exit 0、0 项检查 0 失败；且留痕只写 stderr，而 gate.yml 的 `--json`
    重定向只收 stdout，消费方看不到少跑了断言。

    真正该修的是豁免的**判据**（root_worktree_status），不是加开关。
    """
    text = io.open(d.__file__, encoding="utf-8").read()
    # 解释性注释里允许提到这个名字（那是本次修复的由来），但**代码路径**
    # 上不得再有该开关：查 argparse 注册与 run() 签名。
    assert 'add_argument(\n        "--skip-group"' not in text
    assert "def run(repo_root=\".\", skip_groups" not in text
    assert "skip_groups=args.skip_group" not in text
    assert "self.skipped" not in text
    assert "skip = {g.strip() for g in skip_groups" not in text

    import subprocess
    import sys

    proc = subprocess.run(
        [sys.executable, d.__file__, "--help"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    assert "--skip-group" not in proc.stdout, "CLI 帮助里不应再出现 --skip-group"


def test_mergeguard_does_not_pass_skip_group_to_discipline():
    """mergeguard 合并时**不得**再对 discipline 传 --skip-group。

    合并期间根工作区是脏的，但 rootworktree 组的豁免判据已能正确识别
    「受控合并进行中」—— mergeguard 此刻持有合并锁、锁持有者 pid 存活、
    且无未合并条目，那一组自己就放行。所以不需要、也不允许再有第二层豁免。
    """
    cli = Path(d.__file__).resolve().parents[1] / "mergeguard" / "cli.py"
    if not cli.exists():  # discipline 的测试可能在只装了 discipline 的环境跑
        return
    text = io.open(cli, encoding="utf-8").read()
    # 同理：注释里可以解释「为什么不再传」，但门禁调用里不得再出现它。
    gate_calls = text.split("gates = [", 1)[1].split("]", 1)[0]
    assert "skip-group" not in gate_calls, "mergeguard 的门禁调用不应再传 --skip-group"
