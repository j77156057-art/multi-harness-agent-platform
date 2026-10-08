"""操作员 CLI（MVP-09，test_id ``MVP-UI-001``）。

清单对 MVP-09 的完成标准：

> 人类不直接访问数据库即可完成一条完整任务链路；**前端不能绕过 API 改权威字段**。

前半句由本文件的链路测试覆盖；后半句由**源码级断言**覆盖
（``test_cli_never_imports_the_database_or_services``）—— 那条比「跑一遍看看」
更能守住约束，因为绕过 API 的调用往往看起来更省事。

**两处已知缺口**（同 MVP-10a 发现的那一类：R10 §7 没有对应 verb，因此没有 HTTP
通路）—— 见 ``_start_task_in_process`` 与 ``_verify_in_process`` 的说明。
两者都在任务卡与 ``docs/traceability.md`` 里记着，本测试**不给它们开旁路**。
"""

from __future__ import annotations

import io
import json
import os
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.cli.main import EXIT_API_ERROR, EXIT_OK, main
from app.db.connection import Database
from tests.api_support import make_client, scheduler, worktree_manager
from tests.merge_support import register_harness

CLI_DIR = Path(__file__).resolve().parents[1] / "app" / "cli"


def _run_capture(client: TestClient, *argv: str) -> tuple[int, object, str]:
    import contextlib

    out = io.StringIO()
    err = io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = main(list(argv), client=client)
    text = out.getvalue().strip()
    return code, (json.loads(text) if text else None), err.getvalue().strip()


def _ok(client: TestClient, *argv: str) -> object:
    code, body, err = _run_capture(client, *argv)
    assert code == EXIT_OK, f"{argv} failed: {err}"
    return body


# --------------------------------------------------------------- 架构约束


def test_cli_never_imports_the_database_or_services() -> None:
    """清单硬要求：「前端不能绕过 API 改权威字段」。

    绕过 API 的写会同时绕过 fencing 与 `task_events`，让「投影可重建」失真 ——
    所以这条不是风格偏好。用**源码级**断言守它，因为直接调 service 往往看起来
    更省事，而它只会在很久以后以难查的方式出问题。
    """
    forbidden = re.compile(
        r"^\s*(?:from|import)\s+app\.(db|claim|lease|tasks|verify|merge|git|state|plan)\b",
        re.M,
    )
    for path in sorted(CLI_DIR.glob("*.py")):
        source = path.read_text(encoding="utf-8")
        found = forbidden.findall(source)
        assert not found, f"{path.name} 直接 import 了内部模块 {found}；CLI 只能经 HTTP"


def test_cli_only_speaks_to_the_api_prefix() -> None:
    """所有路径都必须落在 `/api/v1` 之下 —— 混入直连路径会绕过 API 的校验层。"""
    source = (CLI_DIR / "main.py").read_text(encoding="utf-8")
    literal_paths = re.findall(r'"(/[a-z0-9_/{}\-]*)"', source)
    for path in literal_paths:
        if path.startswith("/api"):
            continue
        # 允许的相对片段只应作为 API_PREFIX 的后缀出现
        assert path.startswith("/"), path
    assert 'API_PREFIX = "/api/v1"' in source


# --------------------------------------------------------------- 完整链路


def test_operator_completes_a_task_chain_without_touching_the_database(tmp_path: Path) -> None:
    """建群 → 发方案 → 看板 → 领取 → 提交 → 审查，全部经 CLI。

    断言里刻意**不出现任何 SQL**：人类操作员走的就是这条路径。
    """
    client, database = make_client(tmp_path)
    # rules-tighten F5: the review face needs both sides registry-known with
    # distinct providers; h-1 owns, r-1 reviews.
    register_harness(database, "h-1", provider="openai")
    register_harness(database, "r-1", provider="anthropic")

    _ok(client, "project-create", "P-1", "--name", "demo")
    _ok(client, "group-create", "G-1", "--project", "P-1", "--name", "group")
    _ok(client, "plan-create", "PL-1", "--group", "G-1", "--task", "T-1")
    approved = _ok(client, "plan-approve", "PL-1", "--by", "human-1")
    assert "T-1" in approved["ready"]

    board = _ok(client, "board", "--group", "G-1")
    assert [row["task_id"] for row in board["tasks"]] == ["T-1"]

    detail = _ok(client, "task", "T-1")
    assert detail["status"] == "ready"

    claimed = _ok(client, "claim", "--harness", "h-1", "--task", "T-1", "--request-id", "cr-1")
    item = claimed["items"][0]
    assert item["outcome"] == "claimed"
    work = (item["attempt_id"], item["fencing_epoch"])

    _start_task_in_process(database, tmp_path, "T-1", *work)

    commit = _commit_in_worktree(tmp_path, database, work[0], "T-1")

    _ok(
        client,
        "publish",
        "--task",
        "T-1",
        "--attempt",
        work[0],
        "--branch",
        _branch_for(database, work[0]),
        "--commit",
        commit,
        "--epoch",
        str(work[1]),
        "--idempotency-key",
        "pub-1",
    )
    submitted = _ok(
        client,
        "submit",
        "--task",
        "T-1",
        "--attempt",
        work[0],
        "--epoch",
        str(work[1]),
        "--commit",
        commit,
    )
    assert submitted["lease_released"] is True
    assert _status(database, "T-1") == "verifying"

    # verifying → awaiting_review 需要 verify receipt，而 verify 也没有 HTTP 端点
    # （与 start_task 同一类缺口，见 _start_task_in_process 的说明）。
    _verify_in_process(database, tmp_path, "T-1", work[0], commit, work[1])
    assert _status(database, "T-1") == "awaiting_review"

    # 审查者走自己的 CLI 命令
    _ok(client, "review-claim", "--task", "T-1", "--attempt", work[0], "--reviewer", "r-1")
    _ok(
        client,
        "review-submit",
        "--task",
        "T-1",
        "--attempt",
        work[0],
        "--reviewer",
        "r-1",
        "--verdict",
        "approve",
        "--commit",
        commit,
    )


def test_operator_can_read_events_and_chat(tmp_path: Path) -> None:
    """群聊与系统事件 —— 清单 MVP-09 的交付项之一。"""
    client, _database = make_client(tmp_path)
    _ok(client, "project-create", "P-1", "--name", "demo")
    _ok(client, "group-create", "G-1", "--project", "P-1", "--name", "group")
    _ok(client, "plan-create", "PL-1", "--group", "G-1", "--task", "T-1")
    _ok(client, "plan-approve", "PL-1", "--by", "human-1")

    posted = _ok(client, "say", "--group", "G-1", "--author", "alice", "--body", "starting")
    # POST 的响应只回 message_id/seq/mentions/refs，不回正文 —— 要读回来才算落库
    assert posted["message_id"]

    log = _ok(client, "log", "--group", "G-1")
    assert [m["body"] for m in log["messages"]] == ["starting"]

    events = _ok(client, "events", "--since", "0")
    assert any(e["event_name"] == "plan.approve" for e in events["events"]), events


def test_cli_reports_api_errors_with_the_envelope(tmp_path: Path) -> None:
    """API 错误要以错误信封 + 退出码 1 呈现，而不是堆栈回溯。"""
    client, _database = make_client(tmp_path)

    code, _body, err = _run_capture(client, "task", "missing")

    assert code == EXIT_API_ERROR
    payload = json.loads(err)
    assert payload["error"]["code"] == "resource.not_found"


def test_cli_rejects_bad_usage_with_exit_code_two(tmp_path: Path) -> None:
    """参数不对时 argparse 自己退出（退出码 2），不该走到 API。"""
    client, _database = make_client(tmp_path)
    with pytest.raises(SystemExit) as caught:
        main(["plan-create"], client=client)
    assert caught.value.code == 2


# --------------------------------------------------------------- 真实入口


def test_module_entry_point_runs(tmp_path: Path) -> None:
    """`python -m app.cli --help` 必须能跑 —— 证明真实入口可行，而不只是函数可调。"""
    import subprocess
    import sys

    backend = Path(__file__).resolve().parents[1]
    # 子进程的输出编码由**测试自己**钉死，不要求使用者去设环境变量。
    #
    # 两处都要：
    # - `PYTHONIOENCODING=utf-8` 给子进程：Windows 上它的 stdout 在管道里默认
    #   按 cp936 输出，而 `--help` 里有中文。
    # - `encoding="utf-8", errors="replace"` 给本进程：即便子进程仍吐出非 UTF-8
    #   字节，解码也只降级、不抛。少了 `errors=` 时 UnicodeDecodeError 会让
    #   `stdout` 变成 `None`，于是下面那句 `in result.stdout` 抛
    #   `TypeError: argument of type 'NoneType' is not iterable` ——
    #   一个看不出「真正的问题是编码」的失败。
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    result = subprocess.run(
        [sys.executable, "-m", "app.cli", "--help"],
        cwd=backend,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout is not None, "stdout 成了 None（解码失败？请检查 errors= 参数）"
    assert "只经 HTTP" in result.stdout, (
        f"子进程输出不是 UTF-8（拿到的是：{result.stdout[:200]!r}）—— "
        "测试应自己用 PYTHONIOENCODING 钉住子进程，而不是要求使用者设环境变量"
    )


# --------------------------------------------------------------- 辅助与缺口说明


def _start_task_in_process(
    database: Database, tmp_path: Path, task_id: str, attempt_id: str, epoch: int
) -> None:
    """``claimed → in_progress``。**这一步 CLI 做不到，是已知缺口。**

    与 MVP-10a 发现的是同一个：R10 §7 没有对应 verb（只有推送模型的 ``start()``，
    标为「未来才需要」），所以没有任何 HTTP 通路。现有测试一律直接调
    ``SchedulerService.start_task``，这里跟随同一做法并**显式标注**，
    而不是给 CLI 偷偷开一条绕过 API 的口子 —— 那会违反本任务的完成标准。

    缺口已记在 ``docs/traceability.md`` 的 `MVP-ADAPTER-001` 行与任务卡里。
    """
    manager = worktree_manager(tmp_path, database)
    scheduler(database).start_task(
        task_id=task_id, attempt_id=attempt_id, fencing_epoch=epoch, worktrees=manager
    )


def _verify_in_process(
    database: Database,
    tmp_path: Path,
    task_id: str,
    attempt_id: str,
    commit: str,
    epoch: int,
) -> None:
    """产出 verify receipt，把任务推进到 ``awaiting_review``。

    **这一步 CLI 也做不到** —— 与 ``start_task`` 同一类缺口：Verify Worker 没有
    HTTP 端点。清单的完成标准是「建群 → 发方案 → 领取 → 提交 → 审查」，
    而这条链路上有**两个**没有 verb 的步骤（start 与 verify）。
    两者都记在任务卡与 `docs/traceability.md` 里，本测试不替它们开旁路。
    """
    from app.verify.worker import VerifyWorker
    from tests.api_support import verify_policy

    manager = worktree_manager(tmp_path, database)
    base = database.read("SELECT base_commit FROM tasks WHERE task_id = ?", (task_id,))[0][
        "base_commit"
    ]
    VerifyWorker(
        database,
        manager.repo,
        verify_policy(),
        overlay_root=tmp_path / "ov",
        evidence_root=tmp_path / "evidence",
    ).run(
        task_id=task_id,
        attempt_id=attempt_id,
        commit=commit,
        base_commit=base,
        verify_profile="always-pass",
        fencing_epoch=epoch,
    )


def _branch_for(database: Database, attempt_id: str) -> str:
    rows = database.read("SELECT branch FROM workspaces WHERE attempt_id = ?", (attempt_id,))
    assert rows, f"no workspace for {attempt_id}"
    return str(rows[0]["branch"])


def _commit_in_worktree(tmp_path: Path, database: Database, attempt_id: str, task_id: str) -> str:
    from app.git.repo import run_git

    manager = worktree_manager(tmp_path, database)
    worktree = manager.workspace_for_attempt(attempt_id)
    assert worktree is not None
    (worktree.path / "work.txt").write_text(f"{task_id}\n", encoding="utf-8")
    run_git("add", "work.txt", cwd=worktree.path)
    run_git("commit", "-m", f"feat: {task_id}", cwd=worktree.path)
    return run_git("rev-parse", "HEAD", cwd=worktree.path).stdout.strip()


def _status(database: Database, task_id: str) -> str:
    return str(database.read("SELECT status FROM tasks WHERE task_id = ?", (task_id,))[0]["status"])
