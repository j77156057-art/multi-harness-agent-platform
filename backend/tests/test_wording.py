"""平台上的"翻译"：规则层与模型层。

钉住三件事：

1. **认得出的翻成人话**（失败原因、事件名、状态、窗口 id、绝对路径、任务号）；
2. **认不出的原样返回**（``translated: False``）—— 宁可原样，也不编一个错的说法；
3. **模型层翻不出来就明确失败**，不给半句假话；翻过的走缓存，不再问模型。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.wording import service


def test_a_failure_reason_becomes_plain_words() -> None:
    result = service.translate("harness CLI exited 1", kind="reason")
    assert result["translated"] is True
    assert result["text"] == "窗口的命令退出了（退出码 1）"
    assert result["original"] == "harness CLI exited 1"


def test_a_timeout_reason_says_what_took_too_long() -> None:
    result = service.translate("harness timeout: C:\\x\\codex.CMD exceeded 1800s", kind="reason")
    assert result["translated"] is True
    assert "超时" in result["text"]


def test_an_event_name_becomes_a_sentence() -> None:
    assert service.translate("submit_result", kind="event")["text"] == "交上去了"
    assert service.translate("plan.approve", kind="event")["text"] == "方案批准了"


def test_a_status_becomes_a_word() -> None:
    assert service.translate("awaiting_review", kind="status")["text"] == "等着审"


def test_a_harness_id_becomes_its_registered_name() -> None:
    names = {"qoder-window-admin": "Qoder 窗口管理员"}
    result = service.translate("qoder-window-admin", kind="harness", names=names)
    assert result["translated"] is True
    assert result["text"] == "Qoder 窗口管理员"


def test_an_absolute_path_becomes_its_file_name() -> None:
    result = service.translate("D:\\study\\tools\\agent\\plan-6baef64d-r1.md", kind="path")
    assert result["translated"] is True
    assert result["text"] == "plan-6baef64d-r1.md"


def test_a_task_id_becomes_a_short_form() -> None:
    result = service.translate("FD-8665828938c488fd", kind="id")
    assert result["translated"] is True
    assert "86658289" in result["text"]


def test_an_unknown_text_is_kept_as_it_is() -> None:
    """认不出的**原样返回**：不编。"""
    result = service.translate("某句没人认识的话", kind="auto")
    assert result["translated"] is False
    assert result["text"] == "某句没人认识的话"
    assert result["original"] == "某句没人认识的话"


def test_the_kind_is_guessed_when_not_given() -> None:
    assert service.translate("submit_result")["kind"] == "event"
    assert service.translate("awaiting_review")["kind"] == "status"
    assert service.translate("harness CLI exited 1")["kind"] == "reason"


def test_a_batch_is_translated_in_one_go() -> None:
    rows = service.translate_many(["submit_result", "FD-8665828938c488fd", "看不懂"])
    assert [row["translated"] for row in rows] == [True, True, False]


def test_the_model_layer_summarizes_and_caches(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(service.settings, "data_dir", str(tmp_path), raising=False)
    calls: list[str] = []

    def invoke_model(prompt: str) -> tuple[dict[str, object], dict[str, object]]:
        calls.append(prompt)
        return {"summary": "把开工那几步重新理了一遍"}, {"adapter": "qoderclicn-print"}

    first = service.summarize("一堆技术原文", invoke_model=invoke_model)
    assert first["summary"] == "把开工那几步重新理了一遍"
    assert first["cached"] is False
    assert len(calls) == 1

    # 同一份文本再问一次：走缓存，不再问模型。
    second = service.summarize("一堆技术原文", invoke_model=invoke_model)
    assert second["summary"] == first["summary"]
    assert second["cached"] is True
    assert len(calls) == 1


def test_the_model_layer_fails_loudly_instead_of_guessing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """模型没给出摘要 → 明确失败，不给半句假话。"""
    monkeypatch.setattr(service.settings, "data_dir", str(tmp_path), raising=False)

    def invoke_model(_prompt: str) -> tuple[dict[str, object], dict[str, object]]:
        return {}, {"adapter": "x"}

    with pytest.raises(ValueError):
        service.summarize("原文", invoke_model=invoke_model)


def test_the_cache_is_a_file_under_the_data_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(service.settings, "data_dir", str(tmp_path), raising=False)
    service.summarize("原文", invoke_model=lambda _p: ({"summary": "一句人话"}, {"adapter": "x"}))
    document = json.loads((tmp_path / "wording-cache.json").read_text(encoding="utf-8"))
    assert len(document) == 1


# --------------------------------------------------------------- HTTP 面


def test_the_http_face_translates_and_only_humans_may_ask(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.adapter.protocol import HarnessRegistration
    from app.adapter.registry import HarnessRegistry
    from app.auth.tokens import TokenService
    from app.config import settings
    from tests.api_support import make_client

    monkeypatch.setattr(settings, "auth_mode", "enforce", raising=False)
    client, database = make_client(tmp_path)
    HarnessRegistry(database).register(
        HarnessRegistration(
            harness_id="h-A",
            name="h-A",
            provider="openai",
            adapter="codex",
            adapter_version="1.0",
            capabilities=("implementation",),
            registration_version="reg-1",
        )
    )
    human = TokenService(database).issue(subject_type="human", subject_id="operator")
    harness = TokenService(database).issue(subject_type="harness", subject_id="h-A")

    answered = client.post(
        "/api/v1/wording/translate",
        json={"texts": ["harness CLI exited 1", "看不懂的一句"]},
        headers={"Authorization": f"Bearer {human}"},
    )
    assert answered.status_code == 200, answered.text
    rows = answered.json()["texts"]
    assert rows[0]["text"] == "窗口的命令退出了（退出码 1）"
    assert rows[1]["translated"] is False
    assert rows[1]["text"] == "看不懂的一句"

    refused = client.post(
        "/api/v1/wording/translate",
        json={"texts": ["harness CLI exited 1"]},
        headers={"Authorization": f"Bearer {harness}"},
    )
    assert refused.status_code == 403, "harness 主体不该能让人翻日志"


def test_the_http_face_rejects_a_bad_batch(tmp_path: Path) -> None:
    from app.auth.tokens import TokenService
    from tests.api_support import make_client

    client, database = make_client(tmp_path)
    human = TokenService(database).issue(subject_type="human", subject_id="operator")
    bad = client.post(
        "/api/v1/wording/translate",
        json={"texts": "not a list"},
        headers={"Authorization": f"Bearer {human}"},
    )
    assert bad.status_code == 400, bad.text
