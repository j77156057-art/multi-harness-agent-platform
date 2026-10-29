"""管理员 harness：身份、权限与"真的调模型细化"这三件事的回归。

本部署的角色模型是"群主只提自然语言要求，管理员 harness 细化+分派+审核"。
平台只有 ``human``/``harness`` 两种主体，所以管理员身份不能靠主体类型表达，
必须靠一张受配置的主体白名单（``AP_ADMIN_SUBJECTS``）。这些测试钉住三件事：

1. 白名单是**加性**的：它只放宽 §17 里已经存在的键，绝不制造新权限；白名单为空
   时任何 harness 都拿不到管理员权限（fail-closed）。
2. 细化必须真的经过模型通道并**严格校验**：路径不合法、执行者不在册、输出不是
   合法 JSON，一律明确失败，绝不返回一个"看起来像规格"的降级结果。
3. 细化事件的 actor 是管理员 harness 自己，不是人类操作者。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.adapter.harnesses.cli import CliRunResult
from app.admin.service import AdminHarnessError, AdminService, extract_json_object
from app.api.app import create_app
from app.auth.policy import decide
from app.auth.tokens import Principal, TokenService
from app.config import settings
from tests.claim_support import DEFAULT_GROUP
from tests.merge_support import build_chain, register_harness

LOOPBACK = "http://127.0.0.1"
ADMIN = "office-admin"
MEMBER = "qoder-member-1"

SPEC = {
    "tasks": [
        {
            "title": "补一份启动说明",
            "goal": "在 docs/startup.md 写明启动步骤",
            "exclusive_paths": ["docs/startup.md"],
            "acceptance": "docs/startup.md 存在且包含三条启动步骤",
            "harness_id": MEMBER,
        }
    ]
}


@dataclass
class ScriptedRunner:
    """Stands in for the admin harness's CLI (no model in unit tests)."""

    stdout: str
    returncode: int = 0
    stderr: str = ""
    calls: list[dict] = field(default_factory=list)

    def run(
        self,
        argv,
        *,
        cwd,
        timeout_seconds,
        on_tick=None,
        tick_interval_seconds=None,
        stdin_text=None,
    ):
        self.calls.append(
            {"argv": list(argv), "cwd": Path(cwd), "timeout": timeout_seconds, "stdin": stdin_text}
        )
        return CliRunResult(
            argv=list(argv),
            returncode=self.returncode,
            stdout=self.stdout,
            stderr=self.stderr,
            duration_seconds=0.01,
        )


def _envelope(payload: dict) -> str:
    """One CLI JSON line for either envelope shape.

    ``qoderclicn --output-format json`` answers under ``result``; codex's JSONL
    stream answers under ``msg``. The refine call must parse *each channel's own*
    envelope, so the fixture carries both keys -- with one key, one of the
    channels would look like it produced nothing.
    """
    text = json.dumps(payload, ensure_ascii=False)
    return json.dumps({"result": text, "msg": text})


def _admin_chain(tmp_path):
    """A chain whose registry knows the admin harness *and* the member it assigns to."""
    chain = build_chain(tmp_path)
    register_harness(chain.database, ADMIN, provider="qoder")
    # Different provider from the default administrator below: refine now
    # enforces the same cross-provider boundary the review policy will apply,
    # so an assignable member must be reviewable by this administrator.
    register_harness(chain.database, MEMBER, provider="tencent")
    # A member that holds no platform credential cannot claim a task, so the
    # administrator must not be offered it (see
    # test_a_member_without_a_credential_is_not_offered_work).
    _issue_credential(chain.database, MEMBER)
    return chain


def _issue_credential(database, harness_id: str) -> str:
    """Give a registered window a platform credential, as a deployment would."""
    from app.auth.tokens import TokenService

    return TokenService(database).issue(
        subject_type="harness", subject_id=harness_id, label="test credential"
    )


#: 管理员 Harness 必须是一个**窗口身份**（R10-fix-15）：细化通道由身份解析，
#: `office-admin` 这类模型身份解析不出 CLI，会 fail closed。权限面的测试仍用
#: `ADMIN`（身份与通道是两件事），调用模型的测试用下面这个真实窗口。
ADMIN_WINDOW = "qoder-window-admin"


def _refine_service(
    chain, runner, *, admin: str = ADMIN_WINDOW, scratch: Path | None = None
) -> AdminService:
    """细化服务的夹具：默认管理员是一个注册过的窗口，且通道固定为 qoder。"""
    register_harness(chain.database, admin, provider="qoder")
    _issue_credential(chain.database, admin)
    return AdminService(
        chain.database,
        harness_id=admin,
        runner=runner,
        scratch_dir=scratch or chain.tmp_path / "admin-scratch",
        timeout_seconds=30,
    )


# ------------------------------------------------------------------ 权限矩阵


def _decide(subject_type: str, subject_id: str, key: str, admins: frozenset[str]):
    return decide(Principal(subject_type=subject_type, subject_id=subject_id), key)


@pytest.mark.parametrize("key", ["plan:approve", "admin:refine"])
def test_configured_admin_harness_may_use_admin_keys(monkeypatch, key):
    monkeypatch.setattr(settings, "admin_subjects", frozenset({ADMIN}))
    assert _decide("harness", ADMIN, key, settings.admin_subjects).allowed


def test_member_harness_may_not_use_admin_keys(monkeypatch):
    monkeypatch.setattr(settings, "admin_subjects", frozenset({ADMIN}))
    for key in ("plan:approve", "admin:refine"):
        decision = _decide("harness", MEMBER, key, settings.admin_subjects)
        assert not decision.allowed and decision.reason == "role_not_allowed"


def test_empty_allowlist_gives_no_harness_admin_rights(monkeypatch):
    monkeypatch.setattr(settings, "admin_subjects", frozenset())
    assert not _decide("harness", ADMIN, "plan:approve", settings.admin_subjects).allowed
    assert not _decide("harness", ADMIN, "admin:refine", settings.admin_subjects).allowed


def test_allowlist_does_not_invent_permissions(monkeypatch):
    """A configured admin must not gain keys the matrix never registered."""
    monkeypatch.setattr(settings, "admin_subjects", frozenset({ADMIN}))
    decision = _decide("harness", ADMIN, "task:reopen", settings.admin_subjects)
    assert not decision.allowed and decision.reason == "role_not_allowed"
    unknown = _decide("harness", ADMIN, "admin:nonexistent", settings.admin_subjects)
    assert not unknown.allowed and unknown.reason == "unknown_permission"


def test_configured_admin_harness_may_cancel_tasks(monkeypatch):
    """§17 的 `task:cancel` 属于人类管理员；白名单里的管理员 harness 获得委托。

    没有这条委托，重复派发、以及"指派给同 provider、必然审核不了"的任务
    都只能晾着：平台助手能列出来，但取消不了。
    """
    monkeypatch.setattr(settings, "admin_subjects", frozenset({ADMIN}))
    assert _decide("harness", ADMIN, "task:cancel", settings.admin_subjects).allowed


def test_member_harness_may_not_cancel_tasks(monkeypatch):
    monkeypatch.setattr(settings, "admin_subjects", frozenset({ADMIN}))
    decision = _decide("harness", MEMBER, "task:cancel", settings.admin_subjects)
    assert not decision.allowed and decision.reason == "role_not_allowed"


def test_human_admin_still_approves_plans(monkeypatch):
    monkeypatch.setattr(settings, "admin_subjects", frozenset())
    assert _decide("human", "office-operator", "plan:approve", settings.admin_subjects).allowed


def test_zero_role_keys_are_known_but_granted_to_nobody(monkeypatch):
    """`admin:refine` 不能靠主体类型拿到，但它也不是"未登记"。

    三张表的分工：POLICY 是 §17 矩阵的逐行镜像（每行至少一个"允许"），
    ADMIN_ONLY_KEYS 承载"知道这个键、但任何类型都拿不到"，ADMIN_HARNESS_KEYS
    才是加性白名单。把 admin:refine 塞回 POLICY 放个空集合会破坏矩阵镜像的不变量。
    """
    from app.auth.policy import ADMIN_HARNESS_KEYS, ADMIN_ONLY_KEYS, POLICY

    assert "admin:refine" in ADMIN_ONLY_KEYS
    assert "admin:refine" not in POLICY
    assert not (ADMIN_ONLY_KEYS & set(POLICY))
    # 零角色键也不在加性表的"类型"维度上放行：白名单只补主体 id 这一维。
    assert ADMIN_HARNESS_KEYS["admin:refine"] == frozenset({"harness"})

    monkeypatch.setattr(settings, "admin_subjects", frozenset({ADMIN}))
    human = _decide("human", "office-operator", "admin:refine", settings.admin_subjects)
    assert not human.allowed and human.reason == "role_not_allowed"
    stranger = _decide("harness", "some-other-member", "admin:refine", settings.admin_subjects)
    assert not stranger.allowed and stranger.reason == "role_not_allowed"


def test_admin_harness_may_review_but_only_when_allowlisted(monkeypatch):
    """管理员 harness 领取/提交审核：权限层放行，名单外一律拒绝。

    用户 2026-10-17 的决定 (j)：admin harness 必须能真的点“领取审核/提交审核”，
    否则 Stage C 的审核闭环只能由人代点。这里钉的是**权限层**：加性表把
    review:claim / review:submit 给了配置过的 harness 主体；真正的
    “谁能审这一条”由平台的 review_policy 决定，不在这张表里。
    """
    from app.auth.policy import ADMIN_HARNESS_KEYS, POLICY

    for key in ("review:claim", "review:submit"):
        # 仍然是 §17 矩阵里的 harness 行，不新开权限键。
        assert key in POLICY
        assert ADMIN_HARNESS_KEYS[key] == frozenset({"harness"})

    monkeypatch.setattr(settings, "admin_subjects", frozenset({ADMIN}))
    for key in ("review:claim", "review:submit"):
        allowed = _decide("harness", ADMIN, key, settings.admin_subjects)
        assert allowed.allowed, key
        # 加性表只补"主体 id"这一维：§17 本来就把这两行给了所有 harness，
        # 所以成员照旧能审——它能不能审**这一条**由 review_policy 决定。
        member = _decide("harness", "qoder-member-1", key, settings.admin_subjects)
        assert member.allowed, key
        # 人类主体不受加性表影响：人工审核仍走 review:human_*。
        human = _decide("human", "office-operator", key, settings.admin_subjects)
        assert not human.allowed and human.reason == "role_not_allowed"

    # 白名单清空时管理员自己的管理员私有键拿不到（fail-closed 默认）。
    # review:claim 不在此列：它是 §17 给所有 harness 的行，清空白名单不会改动矩阵。
    monkeypatch.setattr(settings, "admin_subjects", frozenset())
    for key in ("admin:refine", "plan:approve", "allocation:decide", "plan:reject"):
        empty = _decide("harness", ADMIN, key, settings.admin_subjects)
        assert not empty.allowed and empty.reason == "role_not_allowed", key
    assert _decide("harness", ADMIN, "review:claim", settings.admin_subjects).allowed


# -------------------------------------------------------------------- 细化


def test_refine_returns_validated_specs_and_records_the_event(tmp_path):
    chain = _admin_chain(tmp_path)
    runner = ScriptedRunner(stdout=_envelope(SPEC))
    result = _refine_service(chain, runner).refine(
        requirement="帮我加一份启动说明", group_id=DEFAULT_GROUP
    )

    assert [task["harness_id"] for task in result["tasks"]] == [MEMBER]
    assert result["requirement"] == "帮我加一份启动说明"
    assert result["harness_id"] == ADMIN_WINDOW
    # The requirement really went to the CLI as one argv element.
    assert "帮我加一份启动说明" in runner.calls[0]["argv"][-1]

    (receipt,) = chain.database.read(
        "SELECT sender_type, sender_id, body FROM task_messages WHERE group_id = ?",
        (DEFAULT_GROUP,),
    )
    assert (receipt["sender_type"], receipt["sender_id"]) == ("harness", ADMIN_WINDOW)
    assert "帮我加一份启动说明" in receipt["body"]
    assert "补一份启动说明" in receipt["body"] and MEMBER in receipt["body"]


def test_refine_asks_the_owner_instead_of_guessing(tmp_path):
    """The admin refusing to guess is a normal outcome, not an error.

    ``needs_owner`` must be visible to the owner, so it lands in the group
    conversation like the spec receipt does -- and it must NOT dispatch.
    """
    chain = _admin_chain(tmp_path)
    asked = {"questions": ["交付物要中文还是英文？", "要不要连线上文档中心取数据？"]}
    runner = ScriptedRunner(stdout=_envelope(asked))
    result = _refine_service(chain, runner).refine(
        requirement="顺手加个说明", group_id=DEFAULT_GROUP
    )

    assert result["status"] == "needs_owner"
    assert result["questions"] == asked["questions"]
    assert result["tasks"] == []

    (receipt,) = chain.database.read(
        "SELECT sender_id, body FROM task_messages WHERE group_id = ?", (DEFAULT_GROUP,)
    )
    assert receipt["sender_id"] == ADMIN_WINDOW
    assert asked["questions"][0] in receipt["body"]


def test_asking_wins_over_a_half_guessed_spec(tmp_path):
    """Both keys at once is the dangerous case: the spec rests on an assumption."""
    chain = _admin_chain(tmp_path)
    runner = ScriptedRunner(stdout=_envelope({**SPEC, "questions": ["谁的账号？"]}))
    result = _refine_service(chain, runner).refine(requirement="x", group_id=DEFAULT_GROUP)
    assert result["status"] == "needs_owner" and result["tasks"] == []


def test_a_member_without_a_credential_is_not_offered_work(tmp_path):
    """登记在册 ≠ 能领活：没有凭据的窗口领不了任务，派给它的活永远停在 ready。

    实测过一次：管理员把两个子任务派给了 ``codebuddy`` 与 ``qoder``——它们登记
    在册（所以进了办公室的成员表），但平台没有它们的凭据（真正跑 worker 的是
    ``codebuddy-member-1`` / ``qoder-member-1/2``），那两条任务一直没人领。
    """
    chain = _admin_chain(tmp_path)
    register_harness(chain.database, "codebuddy", provider="tencent")
    runner = ScriptedRunner(stdout=_envelope(SPEC))

    _refine_service(chain, runner).refine(requirement="写一份说明", group_id=DEFAULT_GROUP)

    # The prompt names only the member that can actually claim the work.
    prompt = runner.calls[0]["argv"][-1]
    assert MEMBER in prompt
    assert "codebuddy" not in prompt


def test_refine_refuses_when_no_member_can_claim(tmp_path):
    """没有能领活的成员时，细化必须**明确失败**，不能派给一个永远领不了的窗口。"""
    chain = build_chain(tmp_path)
    register_harness(chain.database, ADMIN, provider="qoder")
    register_harness(chain.database, "codebuddy", provider="tencent")
    runner = ScriptedRunner(stdout=_envelope(SPEC))
    service = _refine_service(chain, runner, admin=ADMIN_WINDOW)

    with pytest.raises(AdminHarnessError) as error:
        service.refine(requirement="写一份说明", group_id=DEFAULT_GROUP)

    assert "credential" in str(error.value)


# ----------------------------------------------------- 模型通道跟着管理员身份走


def test_refine_uses_the_channels_of_the_administrator_identity(tmp_path, monkeypatch):
    """细化走哪条 CLI，由"管理员是谁"决定，不是写死的 qoder。

    这里钉两件事：命令行的第一段是可执行文件（每种 CLI 不同），回执里的
    ``model.adapter`` / ``model.channel`` 就是实际用的那条 —— 换成 codex 管理员
    时不能再报 qoder。
    """
    chain = _admin_chain(tmp_path)
    # 通道解析只看身份形状（<base>-window-<slot>），不需要注册：注册是派单面的事。
    admin = "codex-window-1"
    runner = ScriptedRunner(stdout=_envelope(SPEC))
    result = _refine_service(chain, runner, admin=admin).refine(
        requirement="x", group_id=DEFAULT_GROUP
    )

    argv = runner.calls[0]["argv"]
    assert argv and "qoder" not in Path(argv[0]).name.lower(), argv[0]
    assert result["model"]["channel"] == "codex"
    assert result["model"]["adapter"] != "qoderclicn-print"


def test_refine_refuses_an_identity_with_no_registered_channel(tmp_path):
    """没人认识的 id 不能借"默认通道"混过去：fail closed。"""
    chain = _admin_chain(tmp_path)
    runner = ScriptedRunner(stdout=_envelope(SPEC))
    with pytest.raises(AdminHarnessError, match="no registered CLI channel"):
        _refine_service(chain, runner, admin="mystery-agent").refine(
            requirement="x", group_id=DEFAULT_GROUP
        )
    assert runner.calls == []


def test_refine_channel_can_be_pinned_by_configuration(tmp_path, monkeypatch):
    """`AP_ADMIN_MODEL_CHANNEL` 可以把通道钉到另一条 CLI（部署选择，不是猜）。"""
    chain = _admin_chain(tmp_path)
    monkeypatch.setattr(settings, "admin_model_channel", "codex")
    runner = ScriptedRunner(stdout=_envelope(SPEC))
    result = _refine_service(chain, runner).refine(requirement="x", group_id=DEFAULT_GROUP)
    assert result["model"]["channel"] == "codex"
    assert "qoder" not in Path(runner.calls[0]["argv"][0]).name.lower()


def test_refine_sends_a_long_prompt_on_stdin_for_stdin_only_clis(tmp_path):
    """只保留首行的 CLI（CodeBuddy）必须从 stdin 拿完整提示词。"""
    chain = _admin_chain(tmp_path)
    admin = "codebuddy-member-1"
    # 用窗口 id 选通道，不需要登记：通道解析只看身份形状，注册是派单面的事
    runner = ScriptedRunner(stdout=_envelope(SPEC))
    _refine_service(chain, runner, admin=admin).refine(requirement="x", group_id=DEFAULT_GROUP)
    call = runner.calls[0]
    assert call["stdin"] and "Hard rules" in call["stdin"]
    assert "Hard rules" not in " ".join(call["argv"])


def test_the_owners_answers_are_replayed_to_the_model(tmp_path):
    chain = _admin_chain(tmp_path)
    runner = ScriptedRunner(stdout=_envelope(SPEC))
    result = _refine_service(chain, runner).refine(
        requirement="顺手加个说明",
        group_id=DEFAULT_GROUP,
        answers=["用中文", "不要连外部系统"],
    )
    assert result["status"] == "ok"
    sent = runner.calls[0]["argv"][-1]
    assert "用中文" in sent and "不要连外部系统" in sent
    assert "Do not ask these again" in sent
    assert result["answers"] == ["用中文", "不要连外部系统"]


def test_blank_answers_are_not_replayed(tmp_path):
    chain = _admin_chain(tmp_path)
    runner = ScriptedRunner(stdout=_envelope(SPEC))
    _refine_service(chain, runner).refine(
        requirement="x", group_id=DEFAULT_GROUP, answers=["  ", ""]
    )
    assert "The group owner already answered" not in runner.calls[0]["argv"][-1]


@pytest.mark.parametrize(
    "paths", [["/etc/passwd"], ["docs/../secret.md"], ["docs\\win.md"], ["a:b"]]
)
def test_refine_refuses_out_of_scope_paths(tmp_path, paths):
    chain = _admin_chain(tmp_path)
    bad = {"tasks": [{**SPEC["tasks"][0], "exclusive_paths": paths}]}
    runner = ScriptedRunner(stdout=_envelope(bad))
    with pytest.raises(AdminHarnessError, match="out-of-scope paths"):
        _refine_service(chain, runner).refine(requirement="x", group_id=DEFAULT_GROUP)


def test_refine_refuses_unknown_executor(tmp_path):
    chain = _admin_chain(tmp_path)
    bad = {"tasks": [{**SPEC["tasks"][0], "harness_id": "gpt-7-invented"}]}
    runner = ScriptedRunner(stdout=_envelope(bad))
    with pytest.raises(AdminHarnessError, match="not a registered online member"):
        _refine_service(chain, runner).refine(requirement="x", group_id=DEFAULT_GROUP)


def test_refine_refuses_to_invent_harness_identity(tmp_path):
    """A registered-but-unsupported id is still refused (no adapter could run it)."""
    chain = _admin_chain(tmp_path)
    register_harness(chain.database, "ghost-harness", provider="tencent")
    # It has a credential, so the refusal is about the **missing adapter**, not
    # about it being unable to act.
    _issue_credential(chain.database, "ghost-harness")
    bad = {"tasks": [{**SPEC["tasks"][0], "harness_id": "ghost-harness"}]}
    runner = ScriptedRunner(stdout=_envelope(bad))
    with pytest.raises(AdminHarnessError, match="unsupported harness id"):
        _refine_service(chain, runner).refine(requirement="x", group_id=DEFAULT_GROUP)


def test_refine_refuses_output_without_json(tmp_path):
    chain = _admin_chain(tmp_path)
    runner = ScriptedRunner(stdout=json.dumps({"result": "我觉得应该加一份说明"}))
    with pytest.raises(AdminHarnessError, match="no balanced JSON object"):
        _refine_service(chain, runner).refine(requirement="x", group_id=DEFAULT_GROUP)


def test_refine_refuses_an_empty_task_list(tmp_path):
    chain = _admin_chain(tmp_path)
    runner = ScriptedRunner(stdout=_envelope({"tasks": []}))
    with pytest.raises(AdminHarnessError, match="non-empty 'tasks'"):
        _refine_service(chain, runner).refine(requirement="x", group_id=DEFAULT_GROUP)


def test_refine_refuses_a_nonzero_cli_exit(tmp_path):
    chain = _admin_chain(tmp_path)
    runner = ScriptedRunner(stdout="", returncode=1, stderr="boom")
    with pytest.raises(AdminHarnessError, match="exited 1"):
        _refine_service(chain, runner).refine(requirement="x", group_id=DEFAULT_GROUP)


def test_refine_reports_stdout_when_the_cli_fails_quietly_on_stderr(tmp_path):
    """Codex's provider failures are JSONL on stdout; a bare exit code is useless."""
    chain = _admin_chain(tmp_path)
    runner = ScriptedRunner(stdout='{"type":"turn.failed"}', returncode=1, stderr="")
    with pytest.raises(AdminHarnessError, match="turn.failed"):
        _refine_service(chain, runner).refine(requirement="x", group_id=DEFAULT_GROUP)


def test_refine_refuses_more_tasks_than_allowed(tmp_path):
    chain = _admin_chain(tmp_path)
    two = {"tasks": [SPEC["tasks"][0], {**SPEC["tasks"][0], "title": "再来一个"}]}
    runner = ScriptedRunner(stdout=_envelope(two))
    with pytest.raises(AdminHarnessError, match="more than the 1 allowed"):
        _refine_service(chain, runner).refine(requirement="x", group_id=DEFAULT_GROUP, max_tasks=1)


def test_refine_refuses_an_empty_requirement(tmp_path):
    chain = _admin_chain(tmp_path)
    runner = ScriptedRunner(stdout=_envelope(SPEC))
    with pytest.raises(AdminHarnessError, match="must not be empty"):
        _refine_service(chain, runner).refine(requirement="   ", group_id=DEFAULT_GROUP)


def test_extract_json_object_takes_the_whole_nested_object():
    wrapped = '先说一句\n```json\n{"tasks":[{"a":{"b":[1,{"c":2}]}}]}\n```\n再说一句'
    assert extract_json_object(wrapped) == {"tasks": [{"a": {"b": [1, {"c": 2}]}}]}


def test_extract_json_object_survives_a_brace_inside_a_string():
    text = '{"goal":"写到 docs/{x}.md 里","tasks":[]}'
    assert extract_json_object(text)["goal"] == "写到 docs/{x}.md 里"


# ------------------------------------------------------ 权限：两个方向都通


def test_admin_harness_may_both_approve_and_reject_a_plan(monkeypatch):
    """§17 第 426 行把 `plan.reject` 给了"人类管理员/负责人"。

    管理员 harness 是那个人在部署里的替身，所以 approve 与 reject 必须一起
    放行：只给 approve 等于让管理员只能批不能退，一张不合要求的计划除了晾着
    没有出路。这条同时钉住"不新增权限键"：reject 本来就登记在 POLICY 里。
    """
    from app.auth.policy import ADMIN_HARNESS_KEYS, POLICY

    assert "plan:reject" in POLICY
    assert ADMIN_HARNESS_KEYS["plan:reject"] == frozenset({"harness"})
    monkeypatch.setattr(settings, "admin_subjects", frozenset({ADMIN}))
    assert _decide("harness", ADMIN, "plan:reject", settings.admin_subjects).allowed
    assert _decide("harness", ADMIN, "plan:approve", settings.admin_subjects).allowed
    # 名单外仍然拒绝，人类侧不受影响。
    assert not _decide("harness", MEMBER, "plan:reject", settings.admin_subjects).allowed
    assert _decide("human", "office-operator", "plan:reject", settings.admin_subjects).allowed


def test_admin_harness_key_set_is_exactly_the_delegated_rows():
    """加性表里的每一行都必须是 §17 已经登记过的键，且委托对象只有 harness。

    这条是"加性、不发明权限"的机器断言：以后有人往表里塞一个新键或一种新主体，
    这里先红，而不是等到线上才发现管理员多了一个动作。
    """
    from app.auth.policy import ADMIN_HARNESS_KEYS, ADMIN_ONLY_KEYS, POLICY

    for key, roles in ADMIN_HARNESS_KEYS.items():
        assert roles == frozenset({"harness"}), key
        assert key in POLICY or key in ADMIN_ONLY_KEYS, key


def test_plan_events_record_the_real_actor_type(tmp_path, monkeypatch):
    """审计里不许把 harness 的批准写成"人类批准"。

    ``approve_plan`` / ``reject_plan`` 曾经把 ``actor_type`` 写死成 ``human``。
    管理员 harness 一旦能 approve（早就如此）或 reject（本轮新增），这条假话就会
    进入审计：同一个事件里 ``actor_type=human``、``actor_id=qoder-window-1``。
    这里用一个真实的管理员 harness token 走路由，钉住两条路径都记 ``harness``。
    """
    chain = _admin_chain(tmp_path)
    monkeypatch.setattr(settings, "auth_mode", "enforce")
    monkeypatch.setattr(settings, "admin_subjects", frozenset({ADMIN_WINDOW}))
    register_harness(chain.database, ADMIN_WINDOW, provider="qoder")

    tokens = TokenService(chain.database)
    human = tokens.issue(subject_type="human", subject_id="office-operator")
    admin = tokens.issue(subject_type="harness", subject_id=ADMIN_WINDOW)
    client = TestClient(create_app(chain.database, chain.repo), base_url=LOOPBACK)

    def post(path, body, token):
        return client.post(path, json=body, headers={"Authorization": f"Bearer {token}"})

    assert post("/api/v1/projects", {"project_id": "P-9", "name": "p"}, human).status_code == 200
    assert (
        post("/api/v1/projects/P-9/groups", {"group_id": "G-9", "name": "g"}, human).status_code
        == 200
    )

    def snapshot(plan_id, task_id):
        response = post(
            "/api/v1/groups/G-9/plans",
            {"plan_id": plan_id, "snapshot": {"tasks": [{"id": task_id}]}},
            human,
        )
        assert response.status_code == 200, response.text

    # 两个计划：一个由管理员批准，一个由管理员驳回。
    snapshot("PL-OK", "T-OK")
    approved = post("/api/v1/plans/PL-OK/approve", {"decided_by": ADMIN_WINDOW}, admin)
    assert approved.status_code == 200, approved.text

    snapshot("PL-NO", "T-NO")
    rejected = post(
        "/api/v1/plans/PL-NO/reject",
        {"decided_by": ADMIN_WINDOW, "reason": "范围太大"},
        admin,
    )
    assert rejected.status_code == 200, rejected.text

    rows = chain.database.read(
        "SELECT event_name, actor_type, actor_id FROM task_events"
        " WHERE event_name IN ('plan.approve','plan.reject') ORDER BY seq"
    )
    assert [tuple(row) for row in rows] == [
        ("plan.approve", "harness", ADMIN_WINDOW),
        ("plan.reject", "harness", ADMIN_WINDOW),
    ]


def test_formal_dispatch_records_the_admin_harness_as_actor(tmp_path, monkeypatch):
    """The administrator self-loop dispatches through the same route as a human.

    If the route keeps the old hard-coded ``decided_by_type="human"``, every
    task created by the administrator worker would look like a human hand-off
    in the audit trail, which defeats the whole "named actor" model.
    """
    chain = _admin_chain(tmp_path)
    monkeypatch.setattr(settings, "auth_mode", "enforce")
    monkeypatch.setattr(settings, "admin_subjects", frozenset({ADMIN_WINDOW}))
    register_harness(chain.database, ADMIN_WINDOW, provider="qoder")

    tokens = TokenService(chain.database)
    human = tokens.issue(subject_type="human", subject_id="office-operator")
    admin = tokens.issue(subject_type="harness", subject_id=ADMIN_WINDOW)
    client = TestClient(create_app(chain.database, chain.repo), base_url=LOOPBACK)

    def post(path, body, token):
        return client.post(path, json=body, headers={"Authorization": f"Bearer {token}"})

    assert post("/api/v1/projects", {"project_id": "P-D", "name": "p"}, human).status_code == 200
    assert (
        post("/api/v1/projects/P-D/groups", {"group_id": "G-D", "name": "g"}, human).status_code
        == 200
    )
    dispatched = post(
        "/api/v1/groups/G-D/dispatch",
        {
            "request_id": "admin-goal-1-0",
            "title": "DEMO · 补一条说明",
            "goal": "在 docs/demo.md 写一行说明",
            "harness_id": MEMBER,
            "decided_by": ADMIN_WINDOW,
            "exclusive_paths": ["docs/demo.md"],
            "acceptance": "docs/demo.md 出现该说明",
        },
        admin,
    )
    assert dispatched.status_code == 200, dispatched.text
    row = chain.database.read(
        "SELECT actor_type, actor_id FROM task_events"
        " WHERE event_name = 'plan.approve' ORDER BY seq DESC LIMIT 1"
    )[0]
    assert (row["actor_type"], row["actor_id"]) == ("harness", ADMIN_WINDOW)


# ------------------------------------------------------------------ 路由


def test_route_refines_only_for_the_configured_admin_harness(tmp_path, monkeypatch):
    chain = _admin_chain(tmp_path)
    monkeypatch.setattr(settings, "auth_mode", "enforce")
    monkeypatch.setattr(settings, "admin_subjects", frozenset({ADMIN}))

    seen: list[str] = []

    class StubAdminService:
        def __init__(self, database, *, harness_id):
            self.harness_id = harness_id
            seen.append(harness_id)

        def refine(self, *, requirement, group_id, max_tasks, answers=()):
            assert requirement == "帮我加一份启动说明" and max_tasks == 2
            assert list(answers) == ["先做文档，不要动代码"]
            return {
                "status": "ok",
                "harness_id": self.harness_id,
                "group_id": group_id,
                "tasks": [],
            }

    monkeypatch.setattr("app.api.routes.AdminService", StubAdminService)

    tokens = TokenService(chain.database)
    admin_token = tokens.issue(subject_type="harness", subject_id=ADMIN)
    member_token = tokens.issue(subject_type="harness", subject_id=MEMBER)
    human_token = tokens.issue(subject_type="human", subject_id="office-operator")
    client = TestClient(create_app(chain.database, chain.repo), base_url=LOOPBACK)
    body = {
        "group_id": DEFAULT_GROUP,
        "requirement": "帮我加一份启动说明",
        "max_tasks": 2,
        "answers": ["先做文档，不要动代码"],
    }

    def post(token):
        return client.post(
            "/api/v1/adapter/admin/refine",
            json=body,
            headers={"Authorization": f"Bearer {token}"},
        )

    assert post(admin_token).status_code == 200
    assert seen == [ADMIN]
    assert post(member_token).status_code == 403
    assert post(human_token).status_code == 403


def test_route_refuses_an_admin_subject_that_is_not_registered(tmp_path, monkeypatch):
    """A token can outlive its registration; the route must not trust the token alone."""
    chain = _admin_chain(tmp_path)
    monkeypatch.setattr(settings, "auth_mode", "enforce")
    monkeypatch.setattr(settings, "admin_subjects", frozenset({ADMIN}))
    token = TokenService(chain.database).issue(subject_type="harness", subject_id=ADMIN)
    with chain.database.transaction() as conn:
        # capability_probes / harness_registrations both FK to harnesses (006).
        conn.execute("DELETE FROM capability_probes WHERE harness_id = ?", (ADMIN,))
        conn.execute("DELETE FROM harness_registrations WHERE harness_id = ?", (ADMIN,))
        conn.execute("DELETE FROM harnesses WHERE harness_id = ?", (ADMIN,))
    client = TestClient(create_app(chain.database, chain.repo), base_url=LOOPBACK)
    response = client.post(
        "/api/v1/adapter/admin/refine",
        json={"group_id": DEFAULT_GROUP, "requirement": "x"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "admin.not_registered"


# ----------------------------------------------------- self-loop worker inputs


def test_refine_uses_only_selected_members_and_prefixes_the_project(tmp_path):
    """A project goal names its members; the admin must not assign outsiders."""
    chain = _admin_chain(tmp_path)
    runner = ScriptedRunner(stdout=_envelope(SPEC))
    result = _refine_service(chain, runner).refine(
        requirement="给启动页补一句说明",
        group_id=DEFAULT_GROUP,
        members=[MEMBER],
        project_name="DEMO",
    )
    assert result["status"] == "ok"
    assert result["project_name"] == "DEMO"
    assert result["tasks"][0]["harness_id"] == MEMBER
    assert result["tasks"][0]["title"].startswith("DEMO · ")


def test_refine_excludes_every_configured_admin_window_from_members(tmp_path, monkeypatch):
    chain = _admin_chain(tmp_path)
    monkeypatch.setattr(settings, "admin_subjects", frozenset({ADMIN, ADMIN_WINDOW}))
    runner = ScriptedRunner(stdout=_envelope(SPEC))
    result = _refine_service(chain, runner).refine(
        requirement="给启动页补一句说明",
        group_id=DEFAULT_GROUP,
        members=[ADMIN, MEMBER],
    )
    assert result["members"] == [MEMBER]
    assert result["tasks"][0]["harness_id"] == MEMBER


def test_refine_excludes_members_who_share_the_admins_provider(tmp_path):
    """A same-provider task would be unreviewable under cross_provider.

    The administrator must not dispatch work it is forbidden to review; doing
    so only creates a task that stalls at ``awaiting_review``.
    """
    chain = _admin_chain(tmp_path)
    register_harness(chain.database, "qoder-member-2", provider="qoder")
    runner = ScriptedRunner(stdout=_envelope(SPEC))
    result = _refine_service(chain, runner).refine(
        requirement="给启动页补一句说明",
        group_id=DEFAULT_GROUP,
        members=[MEMBER, "qoder-member-2"],
    )
    assert result["members"] == [MEMBER]
    assert [task["harness_id"] for task in result["tasks"]] == [MEMBER]


def test_refine_refuses_when_only_same_provider_members_remain(tmp_path):
    chain = _admin_chain(tmp_path)
    register_harness(chain.database, "qoder-member-2", provider="qoder")
    # It can act (it has a credential), so what rules it out is the provider.
    _issue_credential(chain.database, "qoder-member-2")
    runner = ScriptedRunner(stdout=_envelope(SPEC))
    with pytest.raises(AdminHarnessError, match="cross-provider"):
        _refine_service(chain, runner).refine(
            requirement="给启动页补一句说明",
            group_id=DEFAULT_GROUP,
            members=["qoder-member-2"],
        )


def test_refine_excludes_the_platform_assistant_from_members(tmp_path, monkeypatch):
    chain = _admin_chain(tmp_path)
    register_harness(chain.database, "office-admin", provider="qoder")
    monkeypatch.setattr(settings, "admin_subjects", frozenset({ADMIN_WINDOW}))
    runner = ScriptedRunner(stdout=_envelope(SPEC))
    result = _refine_service(chain, runner).refine(
        requirement="给启动页补一句说明",
        group_id=DEFAULT_GROUP,
        members=["office-admin", MEMBER],
    )
    assert result["members"] == [MEMBER]
    assert result["tasks"][0]["harness_id"] == MEMBER


def test_refine_refuses_a_project_whose_members_are_all_offline(tmp_path):
    chain = _admin_chain(tmp_path)
    runner = ScriptedRunner(stdout=_envelope(SPEC))
    with pytest.raises(AdminHarnessError, match="no registered online member"):
        _refine_service(chain, runner).refine(
            requirement="x",
            group_id=DEFAULT_GROUP,
            members=["not-registered"],
        )


def _review_service(tmp_path, payload):
    runner = ScriptedRunner(stdout=_envelope(payload))
    service = AdminService(
        None,  # review() does not read the database; it only invokes the model.
        harness_id=ADMIN_WINDOW,
        runner=runner,
        scratch_dir=tmp_path / "admin-scratch",
        timeout_seconds=30,
    )
    return service, runner


def _review_inputs():
    task = {
        "task_id": "FD-0123456789abcdef",
        "attempt_id": "FD-0123456789abcdef.a1",
        "title": "DEMO · 补一条说明",
        "status": "awaiting_review",
    }
    spec = {
        "id": task["task_id"],
        "title": task["title"],
        "goal": "在 docs/demo.md 写一行说明",
        "exclusive_paths": ["docs/demo.md"],
        "acceptance": "docs/demo.md 出现该说明且没有改其他文件",
    }
    diff = {"available": True, "patch": "diff --git a/docs/demo.md b/docs/demo.md\n+hello\n"}
    verification = {"status": "passed", "receipt_id": "rc-1"}
    return task, spec, diff, verification


def test_review_approves_only_from_the_model_verdict(tmp_path):
    service, runner = _review_service(tmp_path, {"verdict": "approve", "findings": []})
    task, spec, diff, verification = _review_inputs()
    result = service.review(
        task=task, spec=spec, diff=diff, verification=verification, project_name="DEMO"
    )
    assert result["verdict"] == "approve"
    assert result["findings"] == []
    prompt = " ".join(str(part) for part in runner.calls[0]["argv"]) + str(
        runner.calls[0]["stdin"] or ""
    )
    assert "Real diff" in prompt
    assert "diff --git a/docs/demo.md" in prompt


def test_review_request_changes_requires_a_concrete_finding(tmp_path):
    service, _ = _review_service(tmp_path, {"verdict": "request_changes", "findings": []})
    task, spec, diff, verification = _review_inputs()
    with pytest.raises(AdminHarnessError, match="requires at least one finding"):
        service.review(
            task=task, spec=spec, diff=diff, verification=verification, project_name="DEMO"
        )


def test_review_normalizes_string_findings(tmp_path):
    service, _ = _review_service(
        tmp_path,
        {"verdict": "request_changes", "findings": ["没有写到指定文件"]},
    )
    task, spec, diff, verification = _review_inputs()
    result = service.review(
        task=task, spec=spec, diff=diff, verification=verification, project_name="DEMO"
    )
    assert result["verdict"] == "request_changes"
    assert result["findings"] == [{"severity": "note", "message": "没有写到指定文件"}]


def _revise_service(tmp_path, payload):
    """The administrator's folding step: same service, a scripted model answer."""
    runner = ScriptedRunner(stdout=_envelope(payload))
    service = AdminService(
        None,  # revise_with_reviews does not read the database.
        harness_id=ADMIN_WINDOW,
        runner=runner,
        scratch_dir=tmp_path / "admin-scratch",
        timeout_seconds=30,
    )
    return service, runner


def test_a_revision_may_not_turn_a_relative_path_into_an_absolute_one(tmp_path):
    """并入意见这一步不能把交付路径改成绝对路径。

    审阅意见里如果提到了某个绝对路径，模型会顺手把它抄进 ``exclusive_paths``；
    那样交付物会落在工作区之外，领取时会被范围检查直接拒掉 —— 而当时看不出
    是谁把路径改坏的。修订只允许**在原有范围内**改写，路径非法就保留原来的。
    """
    service, runner = _revise_service(
        tmp_path,
        {
            "tasks": [
                {
                    "title": "补一份作品页",
                    "goal": "写 portfolio/index.html",
                    "exclusive_paths": ["D:\\study\\tools\\agent\\new agent\\portfolio\\index.html"],
                    "acceptance": "portfolio/index.html 存在",
                    "harness_id": MEMBER,
                }
            ]
        },
    )
    original = [
        {
            "id": "FD-1",
            "title": "补一份作品页",
            "goal": "写 portfolio/index.html",
            "exclusive_paths": ["portfolio/index.html"],
            "acceptance": "portfolio/index.html 存在",
            "harness_id": MEMBER,
        }
    ]
    revised = service.revise_with_reviews(
        plan_id="plan-1",
        goal="写一张作品页",
        tasks=original,
        reviews=[{"harness_id": "reader-1", "body": "仓库根是 D:\\study\\tools\\agent\\new agent"}],
    )
    assert revised[0]["exclusive_paths"] == ["portfolio/index.html"]
    # The rule is also in the prompt, so the model is told before it answers.
    prompt = " ".join(str(part) for part in runner.calls[0]["argv"]) + str(
        runner.calls[0]["stdin"] or ""
    )
    assert "相对路径" in prompt


def test_a_revision_keeps_a_valid_narrower_path(tmp_path):
    """合法的修订照旧生效 —— 这条守卫只挡绝对路径，不是把修订变成摆设。"""
    service, _ = _revise_service(
        tmp_path,
        {
            "tasks": [
                {
                    "title": "补一份作品页",
                    "goal": "写 portfolio/index.html",
                    "exclusive_paths": ["portfolio/index.html", "portfolio/assets"],
                    "acceptance": "portfolio/index.html 存在",
                    "harness_id": MEMBER,
                }
            ]
        },
    )
    original = [
        {
            "id": "FD-1",
            "title": "补一份作品页",
            "goal": "写 portfolio/index.html",
            "exclusive_paths": ["portfolio/index.html"],
            "acceptance": "portfolio/index.html 存在",
            "harness_id": MEMBER,
        }
    ]
    revised = service.revise_with_reviews(
        plan_id="plan-1",
        goal="写一张作品页",
        tasks=original,
        reviews=[{"harness_id": "reader-1", "body": "补一个资源目录"}],
    )
    assert revised[0]["exclusive_paths"] == ["portfolio/index.html", "portfolio/assets"]


def test_review_refuses_an_unknown_verdict(tmp_path):
    service, _ = _review_service(tmp_path, {"verdict": "lgtm", "findings": []})
    task, spec, diff, verification = _review_inputs()
    with pytest.raises(AdminHarnessError, match="verdict must be"):
        service.review(
            task=task, spec=spec, diff=diff, verification=verification, project_name="DEMO"
        )


# --------------------------------------------------- 定向消息（私聊，R10-fix-18）


def test_a_direct_question_is_answered_in_the_same_private_channel(tmp_path):
    """The answer rides the same private channel as the question.

    It is one more ``task_messages`` row carrying the ``direct`` signature -- no
    new event, no new state -- and the group's other members cannot read it.
    """
    from app.tasks.service import SchedulerService

    chain = _admin_chain(tmp_path)
    runner = ScriptedRunner(stdout=_envelope({"answer": "T-1 被放弃，账本里的原因是测试超时。"}))
    service = _refine_service(chain, runner)

    result = service.answer(
        question="T-1 为什么被放弃？", group_id=DEFAULT_GROUP, asker="office-operator"
    )

    assert result["answer"] == "T-1 被放弃，账本里的原因是测试超时。"
    messages = SchedulerService(chain.database).list_messages(
        DEFAULT_GROUP, viewer="office-operator"
    )
    direct = [m for m in messages if "direct" in (m.get("refs") or [])]
    assert len(direct) == 1
    assert direct[0]["sender_type"] == "harness"
    assert direct[0]["sender_id"] == ADMIN_WINDOW
    assert direct[0]["body"] == result["answer"]
    # 定向消息不落进群聊：同群的其他人看不到这一条。
    outsider = SchedulerService(chain.database).list_messages(DEFAULT_GROUP, viewer="someone-else")
    assert [m for m in outsider if "direct" in (m.get("refs") or [])] == []


def test_the_read_face_says_who_a_private_message_went_to(tmp_path):
    """两个私聊线程要分得开，读面就得说得出收件人。

    2026-10-29 的真实演练：发给管理员的私聊与发给助手的私聊**都只带 direct
    签名**，界面按「有没有 direct」挑，助手的回答就出现在与管理员的对话里
    （串台）。收件人是平台早就记下的事实（``message_deliveries``），读面把它
    带出来，界面才能按对方分开。
    """
    from app.tasks.service import SchedulerService

    chain = _admin_chain(tmp_path)
    service = SchedulerService(chain.database)

    service.post_message(
        group_id=DEFAULT_GROUP,
        author_kind="human",
        author_id="office-operator",
        body="给管理员：现在到哪一步了？",
        mentions=[ADMIN_WINDOW],
        refs=["direct"],
        direct=True,
    )
    service.post_message(
        group_id=DEFAULT_GROUP,
        author_kind="human",
        author_id="office-operator",
        body="给助手：现在有几条任务？",
        mentions=["office-assistant"],
        refs=["direct"],
        direct=True,
    )

    messages = service.list_messages(DEFAULT_GROUP, viewer="office-operator")
    by_body = {str(m["body"]): m for m in messages}
    to_admin = by_body["给管理员：现在到哪一步了？"]
    to_assistant = by_body["给助手：现在有几条任务？"]
    assert to_admin["recipients"] == [ADMIN_WINDOW]
    assert to_assistant["recipients"] == ["office-assistant"]
    # 群消息没有收件人（没有定向给谁）——空数组，不是猜。
    service.post_message(
        group_id=DEFAULT_GROUP,
        author_kind="harness",
        author_id=MEMBER,
        body="群里说一声。",
        mentions=[],
    )
    group_message = [
        m for m in service.list_messages(DEFAULT_GROUP, viewer="office-operator")
        if m["body"] == "群里说一声。"
    ][0]
    assert group_message["recipients"] == []


def test_the_private_answer_reads_the_ledger_and_is_told_not_to_invent(tmp_path):
    """Without the ledger, "为什么失败" can only be answered by guessing."""
    chain = _admin_chain(tmp_path)
    runner = ScriptedRunner(stdout=_envelope({"answer": "不知道。"}))
    service = _refine_service(chain, runner)

    service.answer(question="T-1 为什么被放弃？", group_id=DEFAULT_GROUP, asker="office-operator")

    prompt = " ".join(str(part) for part in runner.calls[0]["argv"]) + str(
        runner.calls[0]["stdin"] or ""
    )
    assert "T-1" in prompt
    assert "Never invent" in prompt
    assert "为什么被放弃" in prompt


def test_a_private_answer_without_text_fails_closed(tmp_path):
    """An empty answer is not posted: silence is honest, a blank message is not."""
    from app.tasks.service import SchedulerService

    chain = _admin_chain(tmp_path)
    runner = ScriptedRunner(stdout=_envelope({"answer": "   "}))
    service = _refine_service(chain, runner)

    with pytest.raises(AdminHarnessError, match="no answer text"):
        service.answer(question="在吗", group_id=DEFAULT_GROUP, asker="office-operator")

    assert (
        SchedulerService(chain.database).list_messages(DEFAULT_GROUP, viewer="office-operator")
        == []
    )
