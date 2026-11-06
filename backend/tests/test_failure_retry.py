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
    OutOfScopeSubmission,
    _classify_failure,
    build_goal,
    human_failure_reason,
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


def test_an_out_of_scope_submission_is_not_retryable_and_names_the_path():
    """越界是产物的问题，重试只会再写同一个越界文件；原因里必须带路径。"""
    retryable, reason = _classify_failure(
        OutOfScopeSubmission(["README.md"], ["probe/pi-scope/"]),
        task=_task(),
        manager=None,
        attempt_id="T-1.a1",
    )

    assert retryable is False
    assert reason == "out_of_scope: README.md"


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

    assert "Breakpoint report" in goal
    assert "402" in goal
    assert "Continue from the report below" in goal
    # 这一段是平台整理的，不是派活的人写的 —— 必须标出来。
    assert "assembled by the platform" in goal


def test_a_first_attempt_has_no_breakpoint_report():
    goal = build_goal({"id": "T-1"}, harness_id="opencode")

    assert "Breakpoint report" not in goal
    assert "assembled by the platform" not in goal


# --------------------------------------------------------------- 失败原因说人话


def test_the_reason_falls_back_to_stdout_when_stderr_is_empty():
    """opencode 把真正的原因写在 stdout 的事件流里，stderr 是空的。

    2026-11-01 完整流程自检实测：窗口被指到一个「CLI 名额」模型上，中继回 502 +
    `relay_cli_cannot_use_tools`，整条信息只出现在 stdout 的那一行 JSON 里。原来的
    实现只看 stderr ⇒ 事件里只剩 `retryable failure (CliFailed): the harness CLI
    failed before it could deliver`，人既看不到 502，也不知道该换模型。
    """
    stdout = "\n".join(
        [
            '{"type":"step_start","timestamp":1}',
            '{"type":"error","error":{"type":"provider.internal",'
            '"message":"this model is served by a CLI slot, which can only return text"'
            ',"status":502,"response":{"body":"{\\"error\\":{\\"code\\":\\"relay_cli_cannot_use_tools\\"}}"}}}',
        ]
    )
    reason = human_failure_reason(_cli_failed(stderr="", stdout=stdout))

    assert "CLI slot" in reason, reason
    # 已知的中继拒绝码要翻成「为什么 + 怎么办」，否则人还是不知道该动哪里。
    assert "CLI 名额" in reason, reason
    assert "换一个真实 provider 的模型" in reason, reason


def test_stderr_still_wins_when_it_has_something():
    reason = human_failure_reason(
        _cli_failed(stderr="boom: real reason here", stdout='{"type":"error"}')
    )

    assert "real reason here" in reason


def test_a_plain_error_line_in_stdout_is_used_too():
    reason = human_failure_reason(
        _cli_failed(stderr="", stdout="step 1 ok\nFatal: no space left on device\n")
    )

    assert "no space left" in reason
