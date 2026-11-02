"""人写的一封信（`docs/notes/relay-mode.md` §16）。

三条口径在这一层钉死：

1. **作者只能是写信的那个人本人** —— 接口连"替某个窗口写"的参数都没有；
2. **预检与投递同一套判据** —— 名册、线程、跳数，在落盘之前就说清为什么不行；
3. **平台不冒充** —— 落盘的信作者是人，消息面上那条的作者仍是 `relay` 自己。

另有一条边界：收件人可以是**窗口**也可以是**人**，但人没有 CLI 在轮询，所以人收的是
消息面那一条，不给他落一个永远没人读的收件箱。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.auth.tokens import TokenService
from app.config import settings
from app.messages.relay import parse, parse_letter
from app.relay import letters as letter_text
from app.relay.mailbox import Mailbox
from app.relay.threads import MAX_HOPS, ThreadStore
from app.relay.worker import RelayWorker
from tests.api_support import make_client

OPERATOR = "office-operator"
ADMIN = "codex-window-admin"
WINDOW = "codex"


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


# ------------------------------------------------------------------ 拼信


def test_a_composed_letter_is_read_by_the_post_office():
    """拼出来的文本必须被**投递那一侧**的解析器读懂 —— 否则人写对了也投不出去。"""
    text = letter_text.compose(
        author=OPERATOR,
        recipients=[WINDOW, ADMIN],
        body="这件事要你定方向：A 还是 B。",
    )
    author, thread, body = parse_letter(text)
    assert author == OPERATOR
    assert thread == ""
    plan = parse(body, known={WINDOW, ADMIN}, admins={ADMIN}, sender=OPERATOR)
    assert plan.ok
    assert [(block.recipient, block.body) for block in plan.blocks] == [
        (WINDOW, "这件事要你定方向：A 还是 B。"),
        (ADMIN, "这件事要你定方向：A 还是 B。"),
    ]


def test_compose_writes_the_thread_line_only_when_replying():
    assert "回复 thr-1a2b3c4d" in letter_text.compose(
        author=OPERATOR, recipients=[WINDOW], body="接着谈", reply_to="thr-1a2b3c4d"
    )
    assert "回复" not in letter_text.compose(
        author=OPERATOR, recipients=[WINDOW], body="新的事"
    )


def test_the_body_keeps_its_own_paragraphs():
    """正文里人写的空行要留着 —— 那是段落，不是多余空白。"""
    body = letter_text.clean_body("\n\n第一段\n\n第二段\n\n")
    assert body == "第一段\n\n第二段"


def test_the_filename_sorts_by_time_and_names_the_author():
    name = letter_text.filename(author=OPERATOR)
    assert name.endswith("-" + OPERATOR + ".md")
    assert name[:8].isdigit() and name[8] == "-"


def test_recipients_are_deduped_in_order():
    assert letter_text.dedupe([WINDOW, "", ADMIN, WINDOW]) == [WINDOW, ADMIN]


# ------------------------------------------------------------------ 接口


@pytest.fixture()
def relay_env(tmp_path, monkeypatch):
    """一个真平台（内存客户端）、一个真人令牌、一个窗口、一个临时信箱。"""
    monkeypatch.setattr(settings, "auth_mode", "enforce")
    monkeypatch.setenv("AP_RELAY_MODE", "1")
    root = tmp_path / "relay"
    monkeypatch.setenv("AP_RELAY_ROOT", str(root))

    client, database = make_client(tmp_path)
    with database.transaction() as conn:
        conn.execute(
            "INSERT INTO harnesses (harness_id, provider, adapter, status, created_at)"
            " VALUES (?, 'openai', 'codex', 'online', '2026-11-02T00:00:00Z')",
            (WINDOW,),
        )
    token = TokenService(database).issue(subject_type="human", subject_id=OPERATOR)
    harness_token = TokenService(database).issue(subject_type="harness", subject_id=WINDOW)
    return {
        "client": client,
        "database": database,
        "root": root,
        "token": token,
        "harness_token": harness_token,
    }


def test_a_person_writes_a_letter_and_it_lands_in_the_outbox(relay_env):
    env = relay_env
    response = env["client"].post(
        "/api/v1/relay/letters",
        json={"recipients": [WINDOW], "body": "这件事你来定方向。"},
        headers=bearer(env["token"]),
    )
    assert response.status_code == 200, response.text
    written = response.json()["letter"]
    path = env["root"] / "outbox" / written
    assert path.is_file()
    author, _, body = parse_letter(path.read_text(encoding="utf-8"))
    assert author == OPERATOR, "作者必须是写信的那个人"
    assert WINDOW in body


def test_the_roster_says_who_is_a_window_and_who_is_a_person(relay_env):
    env = relay_env
    response = env["client"].get("/api/v1/relay/roster", headers=bearer(env["token"]))
    assert response.status_code == 200, response.text
    payload = response.json()
    assert [item["id"] for item in payload["windows"]] == [WINDOW]
    assert [item["id"] for item in payload["people"]] == [OPERATOR]
    assert payload["author"] == OPERATOR


def test_an_unknown_recipient_is_refused_before_anything_is_written(relay_env):
    """整封不写：投一半的信，读的人会以为那就是全部。"""
    env = relay_env
    response = env["client"].post(
        "/api/v1/relay/letters",
        json={"recipients": [WINDOW, "nobody"], "body": "试试"},
        headers=bearer(env["token"]),
    )
    assert response.status_code == 400
    detail = response.json()["error"]["details"]
    assert detail["unknown"] == ["nobody"]
    # 回灌：把可选的名字给回去，人才知道该改成什么。
    assert WINDOW in detail["known"]
    assert not list((env["root"] / "outbox").glob("*.md"))


def test_a_harness_token_cannot_write_as_a_person(relay_env):
    """给 harness 开这条写入面 = 允许它绕过名册与跳数直接落盘。"""
    env = relay_env
    response = env["client"].post(
        "/api/v1/relay/letters",
        json={"recipients": [WINDOW], "body": "我是窗口，但我想替人写"},
        headers=bearer(env["harness_token"]),
    )
    assert response.status_code == 403


def test_an_empty_body_is_refused(relay_env):
    env = relay_env
    response = env["client"].post(
        "/api/v1/relay/letters",
        json={"recipients": [WINDOW], "body": "   \n  "},
        headers=bearer(env["token"]),
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "relay.empty_body"


def test_replying_to_a_thread_that_does_not_exist_is_refused(relay_env):
    env = relay_env
    response = env["client"].post(
        "/api/v1/relay/letters",
        json={"recipients": [WINDOW], "body": "接着谈", "reply_to": "thr-deadbeef"},
        headers=bearer(env["token"]),
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "relay.thread_missing"


def test_a_thread_at_the_hop_limit_refuses_a_new_reply(relay_env):
    """跳数是刹车：写之前就拦住，别等投递时才退回。"""
    env = relay_env
    store = ThreadStore(env["root"] / "threads")
    thread_id = "thr-1a2b3c4d"
    for _ in range(MAX_HOPS):
        store.record_hop(thread_id, sender=WINDOW, recipients=[ADMIN])

    response = env["client"].post(
        "/api/v1/relay/letters",
        json={"recipients": [WINDOW], "body": "还有一句", "reply_to": thread_id},
        headers=bearer(env["token"]),
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "relay.hop_exceeded"
    assert not list((env["root"] / "outbox").glob("*.md"))


def test_relay_off_says_so_instead_of_writing_a_letter_nobody_delivers(relay_env, monkeypatch):
    env = relay_env
    monkeypatch.delenv("AP_RELAY_MODE", raising=False)
    response = env["client"].post(
        "/api/v1/relay/letters",
        json={"recipients": [WINDOW], "body": "试试"},
        headers=bearer(env["token"]),
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "relay.disabled"
    assert not list((env["root"] / "outbox").glob("*.md"))


# ------------------------------------------------------------------ 投递


class RecordingClient:
    def __init__(self) -> None:
        self.posts: list[dict] = []

    def post(self, path: str, json: dict):
        self.posts.append({"path": path, "json": json})
        return {"ok": True}


def _post_office(tmp_path: Path, *, windows: set[str], people: set[str]):
    mailbox = Mailbox(root=tmp_path / "relay")
    mailbox.ensure()
    client = RecordingClient()
    worker = RelayWorker(
        mailbox=mailbox,
        client=client,
        group="G-1",
        admins={ADMIN},
        known=set(windows),
        owners=set(people),
    )
    return worker, client, mailbox


def test_a_letter_from_a_person_is_delivered_to_a_window(tmp_path):
    worker, client, mailbox = _post_office(tmp_path, windows={WINDOW, ADMIN}, people={OPERATOR})
    text = letter_text.compose(author=OPERATOR, recipients=[WINDOW], body="你来定方向")
    letter_text.write_letter(mailbox.root, name="by-person.md", text=text)

    results = worker.sweep()

    assert results and results[0]["status"] == "delivered"
    # 收件箱文件名带投递时间戳（同名的信投两次不该互相覆盖）。
    delivered = sorted((mailbox.root / "inbox" / WINDOW).glob("*.md"))
    assert delivered, "窗口收的是收件箱里那份文件"
    assert "来自" in delivered[0].read_text(encoding="utf-8-sig")
    # 消息面那条的作者仍是 relay 自己 —— 平台不冒充人。
    assert client.posts[0]["json"]["author_kind"] == "harness"
    assert client.posts[0]["json"]["author_id"] == "relay"
    assert client.posts[0]["json"]["body"].startswith(f"来自 {OPERATOR}")


def test_a_person_gets_the_message_but_no_inbox_directory(tmp_path):
    """人没有 CLI 在轮询：给他落个目录，只会变成永远没人读的垃圾。"""
    worker, client, mailbox = _post_office(tmp_path, windows={WINDOW}, people={OPERATOR})
    text = letter_text.compose(author=WINDOW, recipients=[OPERATOR], body="给你留一句")
    letter_text.write_letter(mailbox.root, name="to-person.md", text=text)

    results = worker.sweep()

    assert results and results[0]["status"] == "delivered"
    assert client.posts, "人要在消息面上收到这一条"
    assert not (mailbox.root / "inbox" / OPERATOR).exists()


def test_an_author_that_is_neither_window_nor_person_is_returned(tmp_path):
    worker, _, mailbox = _post_office(tmp_path, windows={WINDOW}, people={OPERATOR})
    text = letter_text.compose(author="stranger", recipients=[WINDOW], body="我是谁")
    letter_text.write_letter(mailbox.root, name="from-nobody.md", text=text)

    results = worker.sweep()

    assert results[0]["status"] == "returned"
    assert "既不是已登记的窗口" in str(results[0]["reason"])


def colleague_note(path: Path) -> str:
    return path.read_text(encoding="utf-8-sig")
