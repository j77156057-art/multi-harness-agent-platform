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


# --------------------------------------------------------------- 自由文本


def test_machine_marks_in_a_sentence_become_readable() -> None:
    """结论第一句不该是编号。"""
    raw = (
        "**任务** `FD-0b0bcc415aa9fe2f`（attempt `.a1`）· 提交 `32f84d6` · "
        "改动只落在 `exclusive_paths` 唯一允许的路径上。"
    )
    cleaned = service.humanize_text(raw)
    assert "FD-0b0bcc415aa9fe2f" not in cleaned
    assert "attempt" not in cleaned
    assert "exclusive_paths" not in cleaned
    assert "这条活" in cleaned
    assert "第 1 轮" in cleaned
    assert "唯一允许的路径" in cleaned


def test_a_machine_word_with_its_own_explanation_is_not_repeated() -> None:
    """真数据里这句是"改动只落在 `exclusive_paths` 唯一允许的路径上"。

    直接替换成"允许改的路径"会变成"落在 允许改的路径 唯一允许的路径上"—— 同一件事
    说两遍。所以这一整段要折成一句人话。
    """
    cleaned = service.humanize_text("改动只落在 `exclusive_paths` 唯一允许的路径上。")
    assert cleaned == "改动只落在 唯一允许的路径上。"


def test_a_machine_word_on_its_own_still_becomes_readable() -> None:
    """没有跟着解释的时候，标记本身还是要换成人话。"""
    cleaned = service.humanize_text("本轮只碰了 `exclusive_paths` 里的文件。")
    assert "exclusive_paths" not in cleaned
    assert "允许改的路径" in cleaned


def test_a_worker_error_code_becomes_plain_words() -> None:
    """平台内部错误码是写给日志看的 —— 人读到它只会知道"出错了"。"""
    result = service.translate("formal worker error: HarnessLeaseLost", kind="reason")
    assert result["translated"] is True
    assert "HarnessLeaseLost" not in result["text"]
    assert "租约" in result["text"]


def test_an_unknown_worker_error_code_keeps_the_code() -> None:
    """认不出的代码别硬编 —— 把代码留着，人至少能拿去搜。"""
    result = service.translate("formal worker error: SomethingNew", kind="reason")
    assert result["translated"] is True
    assert "SomethingNew" in result["text"]


def test_a_sentence_without_machine_marks_is_left_alone() -> None:
    raw = "我把 README 改了一遍，然后跑了一遍测试，都过了。"
    assert service.humanize_text(raw) == raw


def test_english_words_in_prose_are_not_touched() -> None:
    """散文里的 commit / harness 不是编号 —— 替掉它们只会把句子改坏。"""
    raw = "Now let me commit the deliverable; the test harness is ready."
    assert service.humanize_text(raw) == raw


def test_a_long_hash_is_shortened_not_replaced() -> None:
    raw = "提交 `32f84d6c1a2b3c4d5e6f7a8b9c0d1e2f3a4b5c6`"
    cleaned = service.humanize_text(raw)
    assert "32f84d6" in cleaned
    assert "c1a2b3c4d" not in cleaned


def test_the_text_kind_cleans_a_whole_paragraph() -> None:
    result = service.translate("任务 FD-8665828938c488fd 由 Codex 窗口在第 2 轮做完。", kind="text")
    assert result["translated"] is True
    assert "FD-8665828938c488fd" not in result["text"]
    assert result["original"].startswith("任务 FD-")


def test_a_window_id_after_a_label_becomes_its_registered_name() -> None:
    """「执行窗口：opencode」—— 小白看到 opencode 不知道那是谁。"""
    cleaned = service.humanize_text(
        "- 执行窗口：opencode\n- 发起：codex-window-admin",
        names={"opencode": "OpenCode", "codex-window-admin": "Codex · 管理员"},
    )
    assert "执行窗口：OpenCode" in cleaned
    assert "发起：Codex · 管理员" in cleaned


def test_the_original_is_echoed_exactly_as_it_was_given() -> None:
    """调用方拿 ``original`` 当回填的钥匙 —— 平台不能偷偷给它去空白。

    方案正文末尾带换行：``original`` 要是被 strip 过，调用方按原文找回来就
    找不到，洗过的人话就回填不回去（真跑踩过）。
    """
    raw = "  任务号：`FD-e88e4c97e92843c9`\n"
    result = service.translate(raw, kind="text")
    assert result["original"] == raw
    assert "FD-e88e4c97e92843c9" not in result["text"]


def test_a_windows_unsigned_exit_code_becomes_the_number_people_say() -> None:
    """Windows 的退出码是无符号的：-1 传上来是 4294967295。"""
    result = service.translate("harness CLI exited 4294967295", kind="reason")
    assert result["text"] == "窗口的命令退出了（退出码 -1）"


def test_a_released_lease_says_what_happened() -> None:
    assert service.translate("harness released", kind="reason")["text"].startswith("租约被放开了")


def test_a_field_name_in_prose_is_not_translated() -> None:
    """方案里那句「attempt_no 是序号，不是 attempt_id」是给人对照口径的。

    把 ``attempt_id`` 换成"轮次"就把话改错了（轮次是 attempt_no）——
    字段名要原样留着。
    """
    raw = "attempt_no 是序号，不是 attempt_id；不含 attempt_id、不含 epoch。"
    assert service.humanize_text(raw) == raw


def test_two_task_ids_keep_their_short_forms() -> None:
    """两个任务号放在一起对比时，"这条活 → 这条活"就是一句废话。"""
    raw = "只有 plan_id 与任务号变了（FD-b0302acf397341cd → FD-e88e4c97e92843c9）"
    cleaned = service.humanize_text(raw)
    assert "FD-b0302acf397341cd" not in cleaned
    assert "FD-e88e4c97e92843c9" not in cleaned
    assert "b0302ac" in cleaned
    assert "e88e4c9" in cleaned
    assert "这条活" not in cleaned


def test_a_reason_with_a_task_id_in_front_is_still_a_reason() -> None:
    """现场报告里就是这么记的：「FD-93c9e4fa07914dbc: harness CLI exited 1」。"""
    result = service.translate("FD-93c9e4fa07914dbc: harness CLI exited 1", kind="reason")
    assert result["text"] == "窗口的命令退出了（退出码 1）"


def test_an_unknown_reason_with_a_task_id_in_front_is_left_alone() -> None:
    """里面认不出来就别去掉前缀 —— 留着比丢意思强。"""
    raw = "FD-93c9e4fa07914dbc: 某种没人认识的原因"
    result = service.translate(raw, kind="reason")
    assert result["translated"] is False
    assert result["text"] == raw


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


def test_the_http_face_knows_the_registered_window_names(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """窗口 id → 登记名。

    真跑才发现的坑：名字其实在**注册表**里（``harness_registrations``），
    读错表就静默地什么都不翻 —— "执行窗口：opencode"还是 opencode。
    """
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
            name="Qoder · 成员1",
            provider="openai",
            adapter="codex",
            adapter_version="1.0",
            capabilities=("implementation",),
            registration_version="reg-1",
        )
    )
    human = TokenService(database).issue(subject_type="human", subject_id="operator")

    named = client.post(
        "/api/v1/wording/translate",
        json={"texts": ["h-A"], "kind": "harness"},
        headers={"Authorization": f"Bearer {human}"},
    )
    assert named.status_code == 200, named.text
    assert named.json()["texts"][0]["text"] == "Qoder · 成员1"

    in_prose = client.post(
        "/api/v1/wording/translate",
        json={"texts": ["- 执行窗口：h-A"], "kind": "text"},
        headers={"Authorization": f"Bearer {human}"},
    )
    assert in_prose.status_code == 200, in_prose.text
    assert in_prose.json()["texts"][0]["text"] == "- 执行窗口：Qoder · 成员1"


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
