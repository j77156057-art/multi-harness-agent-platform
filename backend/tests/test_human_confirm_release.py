"""「人确认过」要留痕：默认通过之外，再补一条看得见的点头记录。

口径（2026-11-03 拍板）：默认通过照旧（approved 之后合入照常走），但"人真的看过并同意"
这件事不能只体现在"没打回"上 —— 那和"人根本没看"分不开。这条动作**不改任务状态**，
只写一条 `task_decisions`（scope `approved_release`），与 `task:attempt_budget_override`
同一个套路：决定行就是审计线索，不扩 R10 §37.7 的封闭事件集合。
"""

from __future__ import annotations

import pytest

from app.human.service import HumanOpsService
from app.spec import TaskStatus
from tests.claim_support import make_database, seed_project, seed_task


def _approved(tmp_path):
    database = make_database(tmp_path)
    seed_project(database)
    seed_task(database, task_id="T-1", status=TaskStatus.APPROVED.value)
    return database, HumanOpsService(database)


def _rows(database):
    return database.read(
        "SELECT * FROM task_decisions WHERE task_id = 'T-1' AND scope = 'approved_release'"
    )


def test_confirming_an_approved_task_leaves_a_decision_without_moving_it(tmp_path):
    database, service = _approved(tmp_path)

    result = service.confirm_release(
        task_id="T-1", decided_by="office-operator", note="我核过验收标准了"
    )

    assert result["already"] is False
    assert result["status"] == TaskStatus.APPROVED.value
    # 状态一个字没动：这是留痕，不是闸门。
    assert (
        str(database.read("SELECT status FROM tasks WHERE task_id = 'T-1'")[0]["status"])
        == TaskStatus.APPROVED.value
    )
    rows = _rows(database)
    assert len(rows) == 1
    assert str(rows[0]["actor"]) == "office-operator"
    assert str(rows[0]["answer"]) == "我核过验收标准了"
    assert int(rows[0]["decided_seq"]) >= 0


def test_confirming_twice_does_not_mint_a_second_row(tmp_path):
    database, service = _approved(tmp_path)

    first = service.confirm_release(task_id="T-1", decided_by="office-operator")
    second = service.confirm_release(task_id="T-1", decided_by="office-operator")

    assert second["already"] is True
    assert second["decision_id"] == first["decision_id"]
    assert len(_rows(database)) == 1, "同一任务同一尝试只留一条，重复点不刷时间"


def test_note_is_optional_but_defaults_to_a_readable_word(tmp_path):
    database, service = _approved(tmp_path)

    service.confirm_release(task_id="T-1", decided_by="office-operator", note="   ")

    assert str(_rows(database)[0]["answer"]) == "confirmed"


def test_only_an_approved_task_can_be_confirmed(tmp_path):
    database = make_database(tmp_path)
    seed_project(database)
    seed_task(database, task_id="T-1", status=TaskStatus.AWAITING_REVIEW.value)
    service = HumanOpsService(database)

    with pytest.raises(Exception) as error:
        service.confirm_release(task_id="T-1", decided_by="office-operator")
    assert "approved" in str(error.value)
    assert _rows(database) == [], "拒绝了就别写"


def test_the_human_face_reads_the_trace_back(tmp_path):
    """界面要能把它读回来显示 —— 写进去却看不见等于没写。"""
    database, service = _approved(tmp_path)
    service.confirm_release(task_id="T-1", decided_by="office-operator", note="看了")

    rows = database.read(
        "SELECT decision_id, actor, answer, scope, decided_seq FROM task_decisions"
        " WHERE task_id = ? ORDER BY decided_seq DESC",
        ("T-1",),
    )
    assert [str(row["scope"]) for row in rows] == ["approved_release"]
    assert str(rows[0]["actor"]) == "office-operator"
