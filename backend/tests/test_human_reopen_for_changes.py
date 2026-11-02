"""「默认通过，只在不同意时打回」：approved 之后人能推翻一次（R10 §10 的 `reopen_for_changes`）。

口径（2026-11-02 由人拍板）：管理员审过就是放行，合入照常走；人手里只留一个"不同意"的
权力，用了就要写清原因。所以这里测的是**打回**这条路，不测"同意" —— 不打回就是同意。

这条边在规范 §10 与状态机里都有（`approved | reopen_for_changes | … | changes_requested`），
但此前**没有任何服务能触发它**。这一组钉住它现在真的通了，并且：
- 只有 `approved` 能打回（别的状态明确拒绝，不静默成功）；
- 理由必填（空串/空白拒绝）；
- 理由进事件 payload（事后答得出"为什么"）；
- 打回之后任务落到 `changes_requested`，执行方可以按返修流程重新开工。
"""

from __future__ import annotations

import pytest

from app.human.service import HumanOpsService
from app.spec import EventKind, TaskStatus
from tests.claim_support import make_database, seed_project, seed_task


def _approved(tmp_path):
    """造一条已经过审的活（审阅本身在别的文件里测，这里只关心 approved 这个起点）。"""
    database = make_database(tmp_path)
    seed_project(database)
    seed_task(database, task_id="T-1", status=TaskStatus.APPROVED.value)
    return database, HumanOpsService(database)


def _status(database, task_id: str = "T-1") -> str:
    return str(database.read("SELECT status FROM tasks WHERE task_id = ?", (task_id,))[0]["status"])


def _events(database, name: str) -> list[dict]:
    rows = database.read(
        "SELECT payload_json FROM task_events WHERE event_name = ? ORDER BY seq", (name,)
    )
    import json

    return [json.loads(row["payload_json"]) for row in rows]


def test_a_human_can_send_an_approved_task_back_for_changes(tmp_path):
    database, service = _approved(tmp_path)

    result = service.reopen_for_changes(
        task_id="T-1", decided_by="office-operator", reason="这一版的验收标准没对上第 3 条"
    )

    assert result["status"] == TaskStatus.CHANGES_REQUESTED.value
    assert result["event"] == EventKind.REOPEN_FOR_CHANGES.value
    assert _status(database) == TaskStatus.CHANGES_REQUESTED.value
    payload = _events(database, EventKind.REOPEN_FOR_CHANGES.value)
    assert len(payload) == 1
    assert payload[0]["reason"] == "这一版的验收标准没对上第 3 条"
    assert payload[0]["decided_by"] == "office-operator"
    # 从哪个状态来的也记下来：事件流里能看出这是"过审之后被打回"，不是审阅时就没过。
    assert payload[0]["from"] == TaskStatus.APPROVED.value


def test_the_sender_is_recorded_as_the_human_actor(tmp_path):
    database, service = _approved(tmp_path)
    service.reopen_for_changes(task_id="T-1", decided_by="office-operator", reason="再来一版")

    row = database.read(
        "SELECT actor_type, actor_id FROM task_events WHERE event_name = ?",
        (EventKind.REOPEN_FOR_CHANGES.value,),
    )[0]
    assert row["actor_type"] == "human"
    assert row["actor_id"] == "office-operator"


def test_a_reason_is_required(tmp_path):
    database, service = _approved(tmp_path)

    with pytest.raises(Exception) as error:
        service.reopen_for_changes(task_id="T-1", decided_by="office-operator", reason="   ")
    assert "reason" in str(error.value)
    # 拒绝了就不许动状态：不能"先改状态再报错"。
    assert _status(database) == TaskStatus.APPROVED.value


def test_only_an_approved_task_can_be_sent_back(tmp_path):
    database = make_database(tmp_path)
    seed_project(database)
    seed_task(database, task_id="T-1", status=TaskStatus.AWAITING_REVIEW.value)
    service = HumanOpsService(database)

    with pytest.raises(Exception) as error:
        service.reopen_for_changes(task_id="T-1", decided_by="office-operator", reason="不同意")
    assert "approved" in str(error.value)
    assert _status(database) == TaskStatus.AWAITING_REVIEW.value


def test_an_unknown_task_is_a_not_found(tmp_path):
    database, service = _approved(tmp_path)

    with pytest.raises(Exception) as error:
        service.reopen_for_changes(task_id="NO-SUCH", decided_by="office-operator", reason="不同意")
    assert "NO-SUCH" in str(error.value) or "unknown" in str(error.value).lower()
