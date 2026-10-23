"""§39 分配与传阅的 HTTP 面（R10-fix-13）。

服务层的规则在 `test_allocation_*` 里逐条钉过。这一组走**真路由**，钉的是只有过 HTTP
才会暴露的三件事：

1. 权限面：开轮/冻结/裁决/撤销只有管理员 harness（`AP_ADMIN_SUBJECTS`）能过，
   成员 harness 一律 403 —— 而且**不**靠"我是管理员"这种字段。
2. 身份绑定：`bind_declared` 让"请求体里写别人的 harness_id"变成 403，而不是匿名写入。
3. 硬门禁在路由层也是**拒绝**（409），不是前端提示：没走完传阅就开分配轮必然失败。
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.api.app import create_app
from app.auth.tokens import TokenService
from app.config import settings
from tests.claim_support import DEFAULT_GROUP
from tests.merge_support import build_chain, register_harness

LOOPBACK = "http://127.0.0.1"
ADMIN = "office-admin"
REVIEWER_A = "qoder-member-1"
REVIEWER_B = "codebuddy-member-1"
SNAPSHOT = "snap-R10-39"
PLAN = "plan-R10-39"
#: `build_chain` 已经在 DEFAULT_GROUP 里建好了 T-1；分配层只看"任务存在且属于本群"，
#: 不看它的状态 —— 这正是 §39 硬规则 6（分配不写 `tasks.status`）的另一面。
TASK = "T-1"


@pytest.fixture
def wired(tmp_path, monkeypatch):
    """Registry + tokens + client, with auth enforced and one configured admin."""
    chain = build_chain(tmp_path)
    for harness_id in (ADMIN, REVIEWER_A, REVIEWER_B):
        register_harness(chain.database, harness_id, provider="qoder")
    monkeypatch.setattr(settings, "auth_mode", "enforce")
    monkeypatch.setattr(settings, "admin_subjects", frozenset({ADMIN}))
    tokens = TokenService(chain.database)

    def header(subject_type: str, subject_id: str) -> dict[str, str]:
        token = tokens.issue(subject_type=subject_type, subject_id=subject_id)
        return {"Authorization": f"Bearer {token}"}

    client = TestClient(create_app(chain.database, chain.repo), base_url=LOOPBACK)
    return {
        "chain": chain,
        "client": client,
        "admin": header("harness", ADMIN),
        "a": header("harness", REVIEWER_A),
        "b": header("harness", REVIEWER_B),
        "human": header("human", "office-operator"),
    }


def _post(wired, path, body, who="admin"):
    return wired["client"].post(f"/api/v1/allocation{path}", json=body, headers=wired[who])


def _ok(response) -> dict:
    assert response.status_code == 200, response.text
    return response.json()


def _run_circulation(wired, *, rounds: int = 2) -> str:
    """Two real rounds over HTTP, ending in ``completed`` -- the hard gate's precondition."""
    circulation_id = _ok(
        _post(
            wired,
            "/circulation/open",
            {
                "group_id": DEFAULT_GROUP,
                "plan_id": PLAN,
                "plan_snapshot_id": SNAPSHOT,
                "reviewer_harness_ids": [REVIEWER_A, REVIEWER_B],
                "opened_by": ADMIN,
            },
        )
    )["circulation_id"]
    for index in range(rounds):
        for who in ("a", "b"):
            harness_id = REVIEWER_A if who == "a" else REVIEWER_B
            _ok(
                _post(
                    wired,
                    "/circulation/review",
                    {
                        "circulation_id": circulation_id,
                        "harness_id": harness_id,
                        "body": f"{harness_id} 第 {index + 1} 轮：这里应该拆成两步",
                    },
                    who=who,
                )
            )
        _ok(
            _post(
                wired, "/circulation/close", {"circulation_id": circulation_id, "harness_id": ADMIN}
            )
        )
        if index < rounds - 1:
            _ok(
                _post(
                    wired,
                    "/circulation/revise",
                    {"circulation_id": circulation_id, "harness_id": ADMIN},
                )
            )
    _ok(
        _post(
            wired, "/circulation/complete", {"circulation_id": circulation_id, "harness_id": ADMIN}
        )
    )
    return circulation_id


# ------------------------------------------------------------------- 权限面


def test_only_the_admin_harness_may_drive_the_round(wired):
    """§17 把开/收/修订/定稿/作废给了"人类管理员/负责人"，部署里那是管理员 harness。

    普通成员 harness 必须够不着这些键 —— 否则"管理员"就只是一个自称。
    """
    for path, body in (
        (
            "/circulation/open",
            {
                "group_id": DEFAULT_GROUP,
                "plan_id": PLAN,
                "plan_snapshot_id": SNAPSHOT,
                "reviewer_harness_ids": [REVIEWER_A],
                "opened_by": REVIEWER_A,
            },
        ),
        (
            "/round/open",
            {"group_id": DEFAULT_GROUP, "plan_snapshot_id": SNAPSHOT, "opened_by": REVIEWER_A},
        ),
        (
            "/decision",
            {
                "round_id": "alloc-x",
                "task_id": TASK,
                "application_id": "app-x",
                "decided_by": REVIEWER_A,
                "expected_revision": 1,
            },
        ),
    ):
        refused = _post(wired, path, body, who="a")
        assert refused.status_code == 403, f"{path} 不该让成员 harness 通过"
        assert refused.json()["error"]["code"] == "auth.permission_denied"


def test_a_harness_outside_the_admin_allowlist_is_still_refused(wired, monkeypatch):
    """白名单是**定向**的：把 A 加进白名单不会顺带放开 B。"""
    monkeypatch.setattr(settings, "admin_subjects", frozenset({REVIEWER_A}))
    allowed = _post(
        wired,
        "/circulation/open",
        {
            "group_id": DEFAULT_GROUP,
            "plan_id": PLAN,
            "plan_snapshot_id": SNAPSHOT,
            "reviewer_harness_ids": [REVIEWER_B],
            "opened_by": REVIEWER_A,
        },
        who="a",
    )
    assert allowed.status_code == 200
    refused = _post(
        wired,
        "/circulation/open",
        {
            "group_id": DEFAULT_GROUP,
            "plan_id": PLAN,
            "plan_snapshot_id": "snap-other",
            "reviewer_harness_ids": [REVIEWER_A],
            "opened_by": REVIEWER_B,
        },
        who="b",
    )
    assert refused.status_code == 403


def test_a_member_cannot_apply_under_someone_elses_harness_id(wired):
    """`bind_declared`：申请只能以自己的身份提。冒名是 403，不是"替他申请"。"""
    refused = _post(
        wired,
        "/application",
        {
            "round_id": "alloc-x",
            "harness_id": REVIEWER_B,
            "task_ids": [TASK],
            "reason": "冒名",
        },
        who="a",
    )
    assert refused.status_code == 403
    # 键是登记过的，所以拒绝原因是"不是你的身份"，不是"没有这个权限"。
    assert refused.json()["error"]["code"] == "auth.permission_denied"


def test_the_human_owner_may_drive_the_round(wired):
    opened = _post(
        wired,
        "/circulation/open",
        {
            "group_id": DEFAULT_GROUP,
            "plan_id": PLAN,
            "plan_snapshot_id": SNAPSHOT,
            "reviewer_harness_ids": [REVIEWER_A],
            "opened_by": "office-operator",
        },
        who="human",
    )
    assert opened.status_code == 200


# ----------------------------------------------------------------- 硬门禁


def test_allocation_is_refused_before_any_completed_circulation(wired):
    """§39 硬规则 1：没传阅就分配 = 409，不是 200 + 一句提醒。"""
    refused = _post(
        wired,
        "/round/open",
        {"group_id": DEFAULT_GROUP, "plan_snapshot_id": SNAPSHOT, "opened_by": ADMIN},
    )
    assert refused.status_code == 409
    body = refused.json()["error"]
    assert body["code"] == "allocation.refused"
    assert "no completed circulation round" in body["message"]


def test_the_full_flow_runs_over_http_and_ends_with_a_claim_authorization(wired):
    """一条真路径：传阅两轮 → 开分配轮 → 申请 → 冻结 → 裁决 → 本人接单 → 可 Claim。"""
    circulation_id = _run_circulation(wired)
    opened = _ok(
        _post(
            wired,
            "/round/open",
            {"group_id": DEFAULT_GROUP, "plan_snapshot_id": SNAPSHOT, "opened_by": ADMIN},
        )
    )
    assert opened["circulation_id"] == circulation_id
    round_id = opened["round_id"]

    applied = _ok(
        _post(
            wired,
            "/application",
            {
                "round_id": round_id,
                "harness_id": REVIEWER_A,
                "task_ids": [TASK],
                "reason": "我来做",
                "body": "计划：先写测试再写实现",
            },
            who="a",
        )
    )
    _ok(_post(wired, "/round/close", {"round_id": round_id, "harness_id": ADMIN}))
    round_revision = _current_round_revision(wired, round_id)

    decided = _ok(
        _post(
            wired,
            "/decision",
            {
                "round_id": round_id,
                "task_id": TASK,
                "application_id": applied["application_id"],
                "decided_by": ADMIN,
                "expected_revision": round_revision,
                "reason": "材料最具体",
            },
        )
    )
    assert decided["allocation_state"] == "invited"

    # 裁决本身不给 Claim 授权 —— 本人还要接单（§39 硬规则 2）。
    before = _ok(
        wired["client"].get(
            "/api/v1/allocation/authorization",
            params={"task_id": TASK, "harness_id": REVIEWER_A},
            headers=wired["a"],
        )
    )
    assert before["authorized"] is False

    _ok(
        _post(
            wired,
            "/accept",
            {"allocation_id": decided["allocation_id"], "harness_id": REVIEWER_A},
            who="a",
        )
    )
    after = _ok(
        wired["client"].get(
            "/api/v1/allocation/authorization",
            params={"task_id": TASK, "harness_id": REVIEWER_A},
            headers=wired["a"],
        )
    )
    assert after["authorized"] is True
    assert after["allocation"]["task_id"] == TASK
    assert after["allocation"]["claimed_at"] is None

    # 候选名单是读接口，群主也能看到材料指纹（§39 硬规则 7）。
    candidates = _ok(
        wired["client"].get(
            "/api/v1/allocation/candidates", params={"round_id": round_id, "task_id": TASK}
        )
    )
    assert [row["harness_id"] for row in candidates["candidates"]] == [REVIEWER_A]
    assert candidates["candidates"][0]["material_hash"]


def test_a_stale_revision_is_refused_over_http(wired):
    """乐观锁在路由层同样生效：拿旧版本号裁决 → 409。"""
    _run_circulation(wired)
    round_id = _ok(
        _post(
            wired,
            "/round/open",
            {"group_id": DEFAULT_GROUP, "plan_snapshot_id": SNAPSHOT, "opened_by": ADMIN},
        )
    )["round_id"]
    applied = _ok(
        _post(
            wired,
            "/application",
            {"round_id": round_id, "harness_id": REVIEWER_A, "task_ids": [TASK]},
            who="a",
        )
    )
    _ok(_post(wired, "/round/close", {"round_id": round_id, "harness_id": ADMIN}))
    refused = _post(
        wired,
        "/decision",
        {
            "round_id": round_id,
            "task_id": TASK,
            "application_id": applied["application_id"],
            "decided_by": ADMIN,
            "expected_revision": 1,
        },
    )
    assert refused.status_code == 409
    assert "expected_revision" in refused.json()["error"]["message"]


def test_the_avoidance_rule_is_reachable_over_http(wired):
    """管理员自己申请了这个任务 → 裁决被拒，且理由指向群主（不是"换个人选"）。"""
    _run_circulation(wired)
    round_id = _ok(
        _post(
            wired,
            "/round/open",
            {"group_id": DEFAULT_GROUP, "plan_snapshot_id": SNAPSHOT, "opened_by": ADMIN},
        )
    )["round_id"]
    applied = _ok(
        _post(
            wired,
            "/application",
            {"round_id": round_id, "harness_id": ADMIN, "task_ids": [TASK]},
            who="admin",
        )
    )
    _ok(_post(wired, "/round/close", {"round_id": round_id, "harness_id": ADMIN}))
    refused = _post(
        wired,
        "/decision",
        {
            "round_id": round_id,
            "task_id": TASK,
            "application_id": applied["application_id"],
            "decided_by": ADMIN,
            "expected_revision": _current_round_revision(wired, round_id),
        },
    )
    assert refused.status_code == 409
    assert "回避" in refused.json()["error"]["message"]


def test_defer_and_invite_need_a_reason_field(wired):
    """请求体层面就要求理由：少字段是 400，不是"记一条空理由的事件"。"""
    _run_circulation(wired)
    round_id = _ok(
        _post(
            wired,
            "/round/open",
            {"group_id": DEFAULT_GROUP, "plan_snapshot_id": SNAPSHOT, "opened_by": ADMIN},
        )
    )["round_id"]
    _ok(_post(wired, "/round/close", {"round_id": round_id, "harness_id": ADMIN}))
    missing = _post(
        wired,
        "/decision/defer",
        {"round_id": round_id, "task_id": TASK, "decided_by": ADMIN},
    )
    assert missing.status_code == 400
    assert missing.json()["error"]["code"] == "request.invalid"


def _current_round_revision(wired, round_id: str) -> int:
    """读接口拿不到轮次版本，所以直接读投影：乐观锁要的是"你看到的那一版"。"""
    return int(
        wired["chain"].database.read(
            "SELECT revision FROM allocation_rounds WHERE round_id = ?", (round_id,)
        )[0]["revision"]
    )
