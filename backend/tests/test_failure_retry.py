"""失败可重试 + 断点报告（step 1）。

平台本来就有这条路的词：``RELEASE`` 边把 ``in_progress`` 放回 ``ready``，
``task:release`` 权限本来就发给 harness。之前成员 worker 在 CLI 失败时走的是
``abandon``（终态），于是"失败一次 = 这条活彻底死掉，只能等人类 reopen"。

这里钉住三件事：

1. **可重试的失败**（harness 或它的 provider 挂了）→ 任务**回到队列**，而不是
   终态；worktree 不立刻回收（清扫器会按规范归档留档）。
2. **不可重试的失败**（平台自己拒绝了越界产物 / CLI 根本不在 PATH 上 / attempt
   预算用完）→ 仍然 abandon。
3. **断点报告**：失败时写一份报告（错误、CLI 原话、动过的文件、diff 摘要），
   下一次 attempt 的工单里带着它；管理员改派时也带着它。
"""

from __future__ import annotations

from app.adapter.formal_worker import (
    _classify_failure,
    build_goal,
    read_handoff_report,
    write_handoff_report,
)
from app.adapter.harnesses.cli import CliFailed, CliRunResult, CliUnavailable, HarnessTimeout


def _task(**overrides):
    task = {
        "task_id": "T-1",
        "max_attempts": 3,
        "attempt_no": 1,
    }
    task.update(overrides)
    return task


def _cli_failed(stderr: str = "", stdout: str = "") -> CliFailed:
    return CliFailed(
        "T-1",
        CliRunResult(
            argv=["claude", "-p", "do it"],
            returncode=1,
            stdout=stdout,
            stderr=stderr,
            duration_seconds=12.0,
        ),
    )


# --------------------------------------------------------------- 分类


def test_a_provider_failure_is_retryable():
    retryable, reason = _classify_failure(
        _cli_failed(stderr="API Error: 402 Insufficient Balance"),
        task=_task(),
        manager=None,
        attempt_id="T-1.a1",
    )

    assert retryable is True
    assert "harness CLI" in reason


def test_a_capacity_shortfall_is_retryable_not_a_death():
    """没有空闲工位**不是这条活的错**。

    平台腾不出手的时候，判死会让一条好活因为"别人占着位子"而消失。正确
    做法是把占位还回去、任务留在队列（跟 provider 类失败同一条路）。
    """
    from app.git.worktree import WorktreeCapacityExceeded

    retryable, reason = _classify_failure(
        WorktreeCapacityExceeded("concurrent worktree cap reached (8/8)"),
        task=_task(),
        manager=None,
        attempt_id="T-1.a1",
    )

    assert retryable is True
    assert "worktree" in reason


class _StubAdapter:
    """Just enough adapter to see which verb the worker used."""

    def __init__(self, *, abandon_raises: bool = False):
        self.abandon_raises = abandon_raises
        self.abandoned: list[str] = []
        self.released: list[str] = []

    def abandon(self, *, task_id: str, reason: str) -> None:
        if self.abandon_raises:
            raise RuntimeError("abandon refused: the task is claimed")
        self.abandoned.append(reason)

    def release(self, *, task_id: str, reason: str) -> None:
        self.released.append(reason)


def test_a_failed_attempt_is_abandoned_when_that_edge_exists():
    from app.adapter.formal_worker import release_or_abandon

    adapter = _StubAdapter()
    outcome = release_or_abandon(adapter, "T-1", "boom")

    assert outcome == "abandoned"
    assert adapter.abandoned and not adapter.released


def test_a_claimed_task_gets_its_claim_back_instead_of_dying():
    """``abandon`` is not an edge out of ``claimed``.

    An attempt that failed before the CLI started leaves the task claimed with
    nobody working on it; the claim has to go back to the queue, or the task is
    a zombie and the window's parallel slot stays taken.
    """
    from app.adapter.formal_worker import release_or_abandon

    adapter = _StubAdapter(abandon_raises=True)
    outcome = release_or_abandon(adapter, "T-1", "boom")

    assert outcome == "released"
    assert adapter.released and not adapter.abandoned


def test_a_refused_submission_is_not_retryable():
    """平台自己拒绝了越界产物：再试一次还是同样的拒绝。"""
    retryable, reason = _classify_failure(
        _cli_failed(stderr="Refused: dirty path outside exclusive_paths"),
        task=_task(),
        manager=None,
        attempt_id="T-1.a1",
    )

    assert retryable is False
    assert "out of scope" in reason


def test_a_missing_binary_is_not_retryable():
    retryable, reason = _classify_failure(
        CliUnavailable("claude: not found on PATH"),
        task=_task(),
        manager=None,
        attempt_id="T-1.a1",
    )

    assert retryable is False
    assert "not available" in reason


def test_a_timeout_is_retryable():
    retryable, _ = _classify_failure(
        HarnessTimeout("claude exceeded 900s"),
        task=_task(),
        manager=None,
        attempt_id="T-1.a1",
    )

    assert retryable is True


def test_a_spent_attempt_budget_is_not_retryable():
    retryable, reason = _classify_failure(
        RuntimeError("something else went wrong"),
        task=_task(attempt_no=3, max_attempts=3),
        manager=None,
        attempt_id="T-1.a3",
    )

    assert retryable is False
    assert "budget" in reason


# --------------------------------------------------------------- 断点报告


def test_the_handoff_report_says_where_the_previous_harness_stopped(tmp_path):
    path = tmp_path / "T-1.handoff.json"

    report = write_handoff_report(
        path,
        task=_task(),
        harness_id="claude-code",
        failure={
            "error_type": "CliFailed",
            "error": "claude exited 1",
            "retry_reason": "the harness CLI failed before it could deliver",
            "stdout": "last words",
            "stderr": "402 Insufficient Balance",
        },
        worktree=None,
        manager=None,
        attempt_id="T-1.a1",
    )

    stored = read_handoff_report(path)
    assert stored is not None
    assert stored["failed_harness"] == "claude-code"
    assert stored["error_type"] == "CliFailed"
    assert "402" in stored["cli_stderr_tail"]
    assert stored["files_touched"] == []
    assert report["archived_worktree"] == "data/archives/T-1.a1/"


def test_the_next_attempt_gets_the_breakpoint_report_in_its_work_order(tmp_path):
    path = tmp_path / "T-1.handoff.json"
    write_handoff_report(
        path,
        task=_task(),
        harness_id="claude-code",
        failure={"error_type": "CliFailed", "error": "died", "stderr": "402", "stdout": ""},
        worktree=None,
        manager=None,
        attempt_id="T-1.a1",
    )
    handoff = read_handoff_report(path)

    goal = build_goal(
        {"id": "T-1", "title": "写一份说明"},
        harness_id="opencode",
        handoff=handoff,
    )

    assert "breakpoint report" in goal
    assert "402" in goal
    assert "Continue from it" in goal


def test_a_first_attempt_has_no_breakpoint_report():
    goal = build_goal({"id": "T-1"}, harness_id="opencode")

    assert "breakpoint report" not in goal
