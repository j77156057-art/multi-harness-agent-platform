"""F3：合并闸门必须读**被测仓库**，且开工前就能回答"它跑不跑得起来"。

045e 现场：``update_ref`` 之后跑到的是平台的 ``docs-check``，落盘的
``repo_source`` 是 ``null``；被测仓库里根本没有 ``docs_profile.py``，于是
合并前后都 ``exit 2``，revert 毫无意义。这里钉住三件事：

1. 解析出的 ``repo_source`` **字符串等于**目标工作树的路径，不是「非空」；
2. 仓库自带 profile 时，闸门在**那棵树**里跑，事件 payload 里查得到
   profile 名 / 解析路径 / 执行目录；
3. profile 的入口文件不在被测仓库里时，**approve 阶段**就拒绝，不是等
   agent 干完再 revert。
"""

from __future__ import annotations

import json
from pathlib import Path

from app.git.repo import CanonicalRepo, run_git
from app.merge.worker import MergeWorker
from app.runtime.driver import load_runtime_policy
from app.verify.profile import VerifyPolicy, resolve_worktree_policy
from tests.api_support import bootstrap, make_client, publish_plan
from tests.merge_support import approve, build_chain


def _policy(command: list[str] | None = None) -> VerifyPolicy:
    return VerifyPolicy.from_mapping(
        {
            "command_roots": ["python"],
            "profiles": {
                "docs-check": {
                    "command": command or ["python", "-c", "raise SystemExit(0)"],
                    "timeout_seconds": 30,
                }
            },
        }
    )


def _repo_policy(profile: str, command: list[str]) -> str:
    return json.dumps(
        {
            "version": "1",
            "integration_profile": profile,
            "profiles": {
                profile: {"command": command, "timeout_seconds": 30},
            },
        }
    )


def _commit_files_on_integration(
    repo: CanonicalRepo, tmp_path: Path, files: dict[str, str], *, tag: str
) -> str:
    """Point the integration branch at a commit carrying ``files`` (scheduler path)."""
    work = tmp_path / f"seed-{tag}"
    work.mkdir(parents=True, exist_ok=True)
    run_git("init", "--initial-branch", "integration", str(work))
    head = repo.ref_commit(repo.integration_ref)
    assert head is not None
    run_git("fetch", "--no-tags", str(repo.path), head, cwd=work)
    run_git("checkout", "-b", "integration", "FETCH_HEAD", cwd=work)
    for name, content in files.items():
        target = work / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    run_git("add", *files, cwd=work)
    run_git("commit", "-m", f"chore: {tag}", cwd=work)
    commit = run_git("rev-parse", "HEAD", cwd=work).stdout.strip()
    repo.import_objects(work, commit)
    repo.update_ref(repo.integration_ref, commit)
    return commit


# ------------------------------------------------------------ 解析链路


def test_repo_source_is_the_target_worktree_not_the_platform(tmp_path):
    """``repo_source`` 解析到目标工作树：断言字符串相等，不是「非空」。

    仓库自带 ``verify-profiles.json`` 时，profile 名与策略文件都来自这棵树。
    """
    worktree = tmp_path / "wt"
    worktree.mkdir()
    (worktree / "verify-profiles.json").write_text(
        _repo_policy("repo-check", ["python", "-c", "raise SystemExit(0)"]), encoding="utf-8"
    )

    resolution = resolve_worktree_policy(
        _policy(),
        platform_source=Path("platform/verify-profiles.json"),
        integration_profile="docs-check",
        worktree=worktree,
    )

    assert resolution.name == "repo-check", "the repository declares its own profile"
    assert resolution.repo_source == str(worktree)
    assert resolution.execution_dir == str(worktree)
    assert resolution.policy_file == str(worktree / "verify-profiles.json")
    assert resolution.origin == "repository"
    payload = resolution.as_payload()
    assert payload["repo_source"] == str(worktree)
    assert payload["execution_dir"] == str(worktree)
    assert payload["integration_profile"] == "repo-check"


def test_repo_source_is_the_worktree_even_on_the_platform_fallback(tmp_path):
    """没有仓库自带 profile 时，兜底的是平台策略，但**跑在哪棵树**仍然写清楚。"""
    worktree = tmp_path / "wt"
    worktree.mkdir()

    resolution = resolve_worktree_policy(
        _policy(),
        platform_source=Path("platform/verify-profiles.json"),
        integration_profile="docs-check",
        worktree=worktree,
    )

    assert resolution.repo_source == str(worktree)
    assert resolution.execution_dir == str(worktree)
    # 「正确失败」与「读错仓库」在日志里必须可分辨：这里是平台的兜底 profile。
    assert resolution.origin == "platform"
    assert resolution.policy_file == str(Path("platform/verify-profiles.json"))


def test_merge_gate_runs_the_profile_from_the_target_worktree(tmp_path):
    """目标仓库带 profile 时闸门 ``exit 0``，payload 里查得到三样东西。"""
    chain = approve(
        build_chain(
            tmp_path,
            extra_files={
                "verify-profiles.json": _repo_policy(
                    "repo-check", ["python", "-c", "raise SystemExit(0)"]
                )
            },
        )
    )
    runtime = load_runtime_policy(None, repo=chain.repo)
    worker = MergeWorker(
        chain.database,
        chain.repo,
        policy=runtime.merge_policy(),
        overlay_root=chain.tmp_path / "merge-ov",
        policy_resolver=runtime.resolve_for_worktree,
    )

    outcome = worker.merge(
        task_id=chain.task_id,
        attempt_id=chain.attempt_id,
        commit=chain.head_commit,
        branch=chain.branch,
    )

    assert outcome.status == "merged"
    rows = chain.database.read(
        "SELECT payload_json FROM task_events WHERE aggregate_id = ?"
        " AND event_name = 'merge_success' ORDER BY seq DESC LIMIT 1",
        (chain.task_id,),
    )
    assert rows, "the merge-success event must carry the check result"
    payload = json.loads(str(rows[0]["payload_json"]))
    # profile 名 + 解析路径 + 执行目录：缺一个，事后就分不清跑的到底是谁的树。
    assert payload["integration_profile"] == "repo-check"
    assert payload["repo_source"] == payload["execution_dir"]
    assert Path(payload["repo_source"]).name.startswith("merge-")
    assert payload["profile_origin"] == "repository"
    assert payload["exit_code"] == 0


# ------------------------------------------------------------ approve 前置


def test_approve_rejects_when_the_profile_entry_is_missing(tmp_path):
    """被测仓库没有 profile 要的入口文件 → approve 当场 409，不进入派单。"""
    client, _ = make_client(tmp_path)
    repo = client.app.state.canonical_repo
    _commit_files_on_integration(
        repo,
        tmp_path,
        {
            "verify-profiles.json": _repo_policy(
                "repo-check", ["python", "docs/check.py"]
            )
        },
        tag="profile-missing",
    )
    bootstrap(client)
    publish_plan(
        client,
        tasks=[{"id": "T-1", "exclusive_paths": ["docs/a.md"]}],
    )

    response = client.post(
        "/api/v1/plans/PL-1/approve", json={"decided_by": "human-1"}
    )

    assert response.status_code == 409, response.text
    error = response.json()["error"]
    assert error["code"] == "plan.profile_unrunnable"
    assert error["retryable"] is False
    assert error["details"]["missing"] == ["docs/check.py"]
    assert error["details"]["profile"] == "repo-check"
    assert error["details"]["repo"] == str(repo.path)
    assert error["details"]["plan_status"] == "draft"


def test_approve_passes_when_the_repository_profile_is_runnable(tmp_path):
    """入口文件在 → approve 通过，并把"闸门将读哪份 profile"记在预检里。"""
    client, _ = make_client(tmp_path)
    repo = client.app.state.canonical_repo
    _commit_files_on_integration(
        repo,
        tmp_path,
        {
            "verify-profiles.json": _repo_policy(
                "repo-check", ["python", "docs/check.py"]
            ),
            "docs/check.py": "raise SystemExit(0)\n",
        },
        tag="profile-present",
    )
    bootstrap(client)
    publish_plan(
        client,
        tasks=[{"id": "T-1", "exclusive_paths": ["docs/a.md"]}],
    )

    preflight = client.get("/api/v1/plans/PL-1/assignability")
    assert preflight.status_code == 200, preflight.text
    assert preflight.json()["profile"] is None
    assert preflight.json()["approvable"] is True

    response = client.post(
        "/api/v1/plans/PL-1/approve", json={"decided_by": "human-1"}
    )
    assert response.status_code == 200, response.text
    assert response.json()["scope_warnings"] == []
