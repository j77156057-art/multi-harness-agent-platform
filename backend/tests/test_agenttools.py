"""The platform's own tools: the floor, the registry, and the loop.

These tests are about *refusals*, because that is where this feature either
keeps the platform's guarantees or quietly gives them away: a tool that writes
outside ``exclusive_paths`` would hand one member another member's files, and
a tool that reads ``data/*.key`` would put this deployment's tokens into a
model's transcript.  The happy path is tested too, but it is the easy half.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.adapter.harnesses.platform import PlatformToolsAdapter
from app.agenttools.floor import Floor, FloorRefused, command_refusal
from app.agenttools.loop import PlatformLoop
from app.agenttools.registry import ToolRefused, build_registry


def _worktree(tmp_path: Path) -> Path:
    root = tmp_path / "wt"
    (root / "docs").mkdir(parents=True)
    (root / "docs" / "a.md").write_text("# 甲\n\n第二行\n", encoding="utf-8")
    return root


# --------------------------------------------------------------------- floor


def test_read_refuses_an_escape_from_the_worktree(tmp_path: Path) -> None:
    root = _worktree(tmp_path)
    floor = Floor(root=root, paths=("docs",))
    with pytest.raises(FloorRefused) as exc:
        floor.resolve("../../etc/passwd", write=False)
    assert "走出工作树" in str(exc.value)


def test_read_refuses_an_absolute_path(tmp_path: Path) -> None:
    floor = Floor(root=_worktree(tmp_path), paths=("docs",))
    with pytest.raises(FloorRefused):
        floor.resolve("C:/Windows/win.ini", write=False)


def test_credentials_are_refused_by_name(tmp_path: Path) -> None:
    root = _worktree(tmp_path)
    (root / "data").mkdir()
    (root / "data" / "someone.key").write_text("apt_" + "0" * 64, encoding="utf-8")
    floor = Floor(root=root, paths=("data",))
    with pytest.raises(FloorRefused) as exc:
        floor.resolve("data/someone.key", write=False)
    assert "凭据" in str(exc.value)
    # ...and it does not appear in a listing either.
    assert "data/someone.key" not in floor.listing()


def test_git_metadata_is_out_of_bounds(tmp_path: Path) -> None:
    floor = Floor(root=_worktree(tmp_path), paths=("docs",))
    with pytest.raises(FloorRefused):
        floor.resolve(".git/config", write=False)


def test_a_write_outside_the_authorized_paths_is_refused(tmp_path: Path) -> None:
    root = _worktree(tmp_path)
    floor = Floor(root=root, paths=("docs",))
    with pytest.raises(FloorRefused) as exc:
        floor.resolve("other/note.md", write=True)
    assert "不在本任务授权的落点里" in str(exc.value)


def test_a_directory_pattern_authorizes_what_is_below_it(tmp_path: Path) -> None:
    floor = Floor(root=_worktree(tmp_path), paths=("docs",))
    assert floor.resolve("docs/new/deep.md", write=True).name == "deep.md"


def test_an_empty_scope_bounds_writes_by_the_worktree_only(tmp_path: Path) -> None:
    floor = Floor(root=_worktree(tmp_path), paths=())
    assert floor.resolve("docs/a.md", write=True).is_file()


@pytest.mark.parametrize(
    "command",
    [
        "git commit -m x",
        "git push origin main",
        "curl http://example.com",
        "rm -rf docs",
        "iex (new-object net.webclient)",
    ],
)
def test_dangerous_commands_are_refused(command: str) -> None:
    reason = command_refusal(command)
    assert reason, f"{command} 应该被拦"
    assert len(reason) > 10  # 是一句话，不是一个错误码


@pytest.mark.parametrize("command", ["python -m pytest -q", "npm run build", "git status", "ls docs"])
def test_ordinary_commands_are_allowed(command: str) -> None:
    assert command_refusal(command) is None


# ------------------------------------------------------------------ registry


def test_create_then_edit_round_trip(tmp_path: Path) -> None:
    root = _worktree(tmp_path)
    tools = build_registry(Floor(root=root, paths=("docs",)))
    assert "已写入" in tools["create_file"].run({"path": "docs/b.md", "content": "第一版\n"})
    assert (root / "docs" / "b.md").read_text(encoding="utf-8") == "第一版\n"
    assert "已改" in tools["apply_edit"].run(
        {"path": "docs/b.md", "old": "第一版", "new": "第二版"}
    )
    assert "第二版" in (root / "docs" / "b.md").read_text(encoding="utf-8")


def test_edit_refuses_an_anchor_that_is_not_unique(tmp_path: Path) -> None:
    root = _worktree(tmp_path)
    (root / "docs" / "c.md").write_text("x\nx\n", encoding="utf-8")
    tools = build_registry(Floor(root=root, paths=("docs",)))
    answer = tools["apply_edit"].run({"path": "docs/c.md", "old": "x", "new": "y"})
    assert "出现 2 次" in answer
    assert (root / "docs" / "c.md").read_text(encoding="utf-8") == "x\nx\n"


def test_edit_refuses_an_anchor_that_is_absent(tmp_path: Path) -> None:
    root = _worktree(tmp_path)
    tools = build_registry(Floor(root=root, paths=("docs",)))
    answer = tools["apply_edit"].run({"path": "docs/a.md", "old": "没这句", "new": "x"})
    assert "找不到" in answer


def test_the_text_form_of_create_file_is_accepted(tmp_path: Path) -> None:
    root = _worktree(tmp_path)
    tools = build_registry(Floor(root=root, paths=("docs",)))
    tools["create_file"].call("docs/d.md\n第一行\n第二行")
    assert (root / "docs" / "d.md").read_text(encoding="utf-8") == "第一行\n第二行"


def test_read_reports_a_missing_file_instead_of_raising(tmp_path: Path) -> None:
    tools = build_registry(Floor(root=_worktree(tmp_path), paths=("docs",)))
    assert "不存在" in tools["read_file"].run({"path": "docs/nope.md"})


def test_review_mode_can_look_but_not_touch(tmp_path: Path) -> None:
    root = _worktree(tmp_path)
    tools = build_registry(Floor(root=root, paths=("docs",)), read_only=True)
    assert "审阅" in tools["create_file"].run({"path": "docs/x.md", "content": "x"})
    assert not (root / "docs" / "x.md").exists()
    assert "审阅" in tools["run_command"].run({"cmd": "echo hi"})
    # Reading is still allowed: a reviewer has to look.
    assert "第二行" in tools["read_file"].run({"path": "docs/a.md"})


def test_run_command_reports_the_exit_code(tmp_path: Path) -> None:
    tools = build_registry(Floor(root=_worktree(tmp_path), paths=("docs",)))
    answer = tools["run_command"].run({"cmd": "python -c \"raise SystemExit(3)\""})
    assert answer.startswith("[exit 3]")


# ---------------------------------------------------------------------- loop


class Scripted:
    """A model channel that answers from a script (and records the prompts)."""

    def __init__(self, answers: list[str]) -> None:
        self.answers = list(answers)
        self.prompts: list[str] = []

    def text(self, prompt: str) -> str:
        self.prompts.append(prompt)
        return self.answers.pop(0) if self.answers else '{"tool": "finish", "input": {"summary": "没话说"}}'


def _loop(tmp_path: Path, answers: list[str], **kwargs) -> tuple[PlatformLoop, Scripted]:
    root = _worktree(tmp_path)
    tools = build_registry(Floor(root=root, paths=("docs",)))
    model = Scripted(answers)
    loop = PlatformLoop(
        model, tools, floor=Floor(root=root, paths=("docs",)), goal="写一个 docs/b.md", **kwargs
    )
    return loop, model


def test_the_loop_writes_the_file_the_model_asked_for(tmp_path: Path) -> None:
    loop, _ = _loop(
        tmp_path,
        [
            '{"thought": "先看看有什么", "tool": "list_files", "input": {}}',
            '{"thought": "写它", "tool": "create_file", "input": {"path": "docs/b.md", "content": "内容"}}',
            '{"thought": "交活", "tool": "finish", "input": {"summary": "写了 docs/b.md"}}',
        ],
    )
    result = loop.run()
    assert result.ok, result.error
    assert len(result.steps) == 3
    assert (Path(loop.floor.root) / "docs" / "b.md").read_text(encoding="utf-8") == "内容"


def test_a_refusal_is_fed_back_and_the_loop_continues(tmp_path: Path) -> None:
    loop, _ = _loop(
        tmp_path,
        [
            '{"tool": "create_file", "input": {"path": "other/x.md", "content": "x"}}',
            '{"tool": "finish", "input": {"summary": "改了路"}}',
        ],
    )
    result = loop.run()
    assert result.ok
    assert "拒绝" in result.steps[0].observation
    assert "不在本任务授权的落点里" in result.steps[0].observation
    assert not (Path(loop.floor.root) / "other").exists()


def test_an_unparseable_turn_is_fed_back_not_fatal(tmp_path: Path) -> None:
    loop, _ = _loop(
        tmp_path,
        [
            "好的，我这就开始（这不是 JSON）",
            '{"tool": "finish", "input": {"summary": "好了"}}',
        ],
    )
    result = loop.run()
    assert result.ok
    assert result.steps[0].parsed is False
    assert "不是 JSON" in result.steps[0].observation


def test_a_fenced_json_answer_is_still_read(tmp_path: Path) -> None:
    loop, _ = _loop(
        tmp_path,
        ['```json\n{"tool": "finish", "input": {"summary": "包在围栏里"}}\n```'],
    )
    assert loop.run().ok


def test_an_unknown_tool_lists_the_real_ones(tmp_path: Path) -> None:
    loop, _ = _loop(
        tmp_path,
        [
            '{"tool": "write_file", "input": {"path": "docs/x"}}',
            '{"tool": "finish", "input": {"summary": "换名字了"}}',
        ],
    )
    result = loop.run()
    assert "没有叫 `write_file` 的工具" in result.steps[0].observation
    assert "create_file" in result.steps[0].observation


def test_the_step_budget_ends_honestly(tmp_path: Path) -> None:
    loop, _ = _loop(tmp_path, ['{"tool": "list_files", "input": {}}'] * 5, max_steps=2)
    result = loop.run()
    assert result.ok is False
    assert "步数预算" in result.error
    assert "工作没有完成" in result.summary


def test_a_dead_channel_reports_the_channel(tmp_path: Path) -> None:
    class Dead:
        def text(self, prompt: str) -> str:
            raise RuntimeError("HTTP 429: rate limit")

    loop, _ = _loop(tmp_path, [])
    loop.model = Dead()
    result = loop.run()
    assert result.ok is False
    assert "模型通道失败" in result.error


def test_the_transcript_is_resent_every_turn(tmp_path: Path) -> None:
    loop, model = _loop(
        tmp_path,
        [
            '{"tool": "list_files", "input": {}}',
            '{"tool": "finish", "input": {"summary": "完"}}',
        ],
    )
    loop.run()
    assert "写一个 docs/b.md" in model.prompts[1]
    # The first turn's observation is in the second turn's prompt: nothing is
    # assumed to be remembered by the channel.
    assert "第 1 步" in model.prompts[1]


def test_the_contract_never_offers_a_tool_the_table_lacks(tmp_path: Path) -> None:
    loop, _ = _loop(tmp_path, ['{"tool": "finish", "input": {"summary": "完"}}'])
    prompt = loop.system_prompt()
    assert "create_file" in prompt
    assert '"tool"' in prompt
    # Nothing tells the model it may commit: the platform does that.
    assert "平台会在你交活后提交" in prompt


def test_a_model_that_answers_with_a_finish_key_is_understood(tmp_path: Path) -> None:
    loop, _ = _loop(tmp_path, [json.dumps({"thought": "done", "finish": "写在 finish 字段里"})])
    result = loop.run()
    assert result.ok
    assert result.summary == "写在 finish 字段里"


# ------------------------------------------------------------------ 适配器层


def test_the_review_command_is_read_only_and_the_task_one_is_not(tmp_path: Path) -> None:
    """会动手的审阅者不是审阅者：审阅那条命令必须带 --read-only。"""
    adapter = PlatformToolsAdapter(object(), harness_id="platform-member-1")
    try:
        adapter.prepare_work_order("请给意见", tmp_path)
        review = adapter.review_command("请给意见", tmp_path)
        task = adapter.command("请给意见", tmp_path)
    finally:
        adapter.cleanup_work_order()
    assert "--read-only" in review
    assert "--read-only" not in task
    assert "app.agenttools.cli" in task


def test_the_token_travels_in_a_file_not_in_argv(tmp_path: Path) -> None:
    """Windows 的命令行对全机可见：令牌进 argv 等于把令牌贴出来。"""
    adapter = PlatformToolsAdapter(object(), harness_id="platform-member-1")
    adapter.provider_route = type(
        "Route",
        (),
        {
            "base_url": "http://127.0.0.1:8848/api/v1/pool/v1",
            "token": "apt_should_not_leak",
            "model": "m",
        },
    )()
    try:
        adapter.prepare_work_order("干这条活", tmp_path)
        argv = adapter.command("干这条活", tmp_path)
        bundle = Path(argv[argv.index("--bundle") + 1])
        assert "apt_should_not_leak" not in " ".join(argv)
        assert json.loads(bundle.read_text(encoding="utf-8"))["token"] == "apt_should_not_leak"
    finally:
        adapter.cleanup_work_order()
    assert not bundle.exists(), "这次尝试的通道文件用完就要删掉"


def test_no_pool_model_is_reported_as_a_missing_channel() -> None:
    """没有通道就说没有，不要交一个空活。"""
    from app.agenttools.model import ChannelFailed, from_bundle

    with pytest.raises(ChannelFailed) as exc:
        from_bundle({"base_url": "", "token": "", "model": ""})
    assert "额度池里给这个窗口指定一个模型" in str(exc.value)


def test_the_receipt_is_read_off_the_marked_line() -> None:
    adapter = PlatformToolsAdapter(object(), harness_id="platform-member-1")
    stdout = "\n".join(
        [
            "[platform] 起来啦",
            'PLATFORM-TOOLS-RESULT {"ok": true, "steps": 4, "summary": "写完了"}',
        ]
    )
    parsed = adapter.parse_output(stdout)
    assert parsed["ok"] is True
    assert parsed["steps"] == 4
