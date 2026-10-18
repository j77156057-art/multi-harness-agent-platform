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
    register_harness(chain.database, MEMBER, provider="qoder")
    return chain


#: 管理员 Harness 必须是一个**窗口身份**（R10-fix-15）：细化通道由身份解析，
#: `office-admin` 这类模型身份解析不出 CLI，会 fail closed。权限面的测试仍用
#: `ADMIN`（身份与通道是两件事），调用模型的测试用下面这个真实窗口。
ADMIN_WINDOW = "qoder-window-admin"


def _refine_service(
    chain, runner, *, admin: str = ADMIN_WINDOW, scratch: Path | None = None
) -> AdminService:
    """细化服务的夹具：默认管理员是一个注册过的窗口，且通道固定为 qoder。"""
    register_harness(chain.database, admin, provider="qoder")
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
    register_harness(chain.database, "ghost-harness", provider="qoder")
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


# ------------------------------------------------------------------ 路由


def test_route_refines_only_for_the_configured_admin_harness(tmp_path, monkeypatch):
    chain = _admin_chain(tmp_path)
    register_harness(chain.database, MEMBER, provider="qoder")
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
