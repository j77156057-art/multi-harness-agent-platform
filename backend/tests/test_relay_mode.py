"""转接模式：解析与投递。

为什么传输是**文件**而不是终端屏幕：2026-11-01 实测，TUI 两分钟产出 2911 字节、173 个
控制序列，用真 VT 模拟器重建之后一行可用的都没有；而写文件是每个 coding CLI 都会做的
事，写出来是干净字节。所以"平台只负责转接"落在文件上：outbox 收信，worker 投递。

这里钉住五件事：

1. 一封信按块拆开，收件人写窗口 id，`管理员` 是别名；
2. **平台不冒充任何人**：消息作者是 relay 自己，正文首行写「来自 <原作者>」；
3. 认不出收件人 / 空块 / 写给自己 ⇒ 整封退回，一条都不投；
4. 投过的信挪进 archive，进程重启也不会重复投；
5. 作者不是已登记窗口 ⇒ 退回（文件名不可信）。
"""

from __future__ import annotations

from pathlib import Path

from app.messages.relay import looks_like_envelope, parse, parse_letter
from app.relay.mailbox import Mailbox
from app.relay.worker import RelayWorker

ADMINS = {"codex-window-admin"}
KNOWN = {"codex", "opencode", "codex-window-admin"}


# ------------------------------------------------------------------ 解析


def test_blocks_are_split_by_header_and_admin_is_an_alias():
    body = "给管理员：\n这件事要你定方向：A 还是 B。\n\n给 opencode：\n我改了 docs/x.md 第 3 段。\n"
    plan = parse(body, known=KNOWN, admins=ADMINS, sender="codex")
    assert plan.ok
    assert [(block.recipient, block.body) for block in plan.blocks] == [
        ("codex-window-admin", "这件事要你定方向：A 还是 B。"),
        ("opencode", "我改了 docs/x.md 第 3 段。"),
    ]


def test_a_plain_message_is_not_an_envelope():
    assert looks_like_envelope("今天的进度还行，明天继续。") is False
    assert looks_like_envelope("\n\n给 opencode：\n接一下\n") is True


def test_an_unknown_recipient_returns_the_whole_letter():
    plan = parse("给 nobody：\n喂\n", known=KNOWN, admins=ADMINS, sender="codex")
    assert not plan.ok
    assert plan.problem == "message.relay_unknown_recipient"
    assert plan.unknown == ("nobody",)
    # 一条都不投：部分投递会让读的人以为那就是全部。
    assert plan.blocks == ()


def test_an_empty_block_or_a_self_block_is_refused():
    assert parse("给 opencode：\n   \n", known=KNOWN, admins=ADMINS, sender="codex").problem == "message.relay_empty_block"
    assert parse("给 codex：\n自己给自己\n", known=KNOWN, admins=ADMINS, sender="codex").problem == "message.relay_self"


def test_a_letter_declares_its_author_on_the_first_line():
    author, body = parse_letter("来自 codex\n\n给 opencode：\n接着干\n")
    assert author == "codex"
    assert body.strip().startswith("给 opencode：")
    # 没有作者行的，作者为空 —— 交给调用方当作"格式不合规"退回，不猜。
    assert parse_letter("给 opencode：\n接着干\n") == ("", "给 opencode：\n接着干\n")


def test_a_bom_before_the_author_line_is_tolerated():
    """Windows 上写 UTF-8 常带 BOM：带 BOM 的信不该被判成"没作者"（实测踩到过）。"""
    author, body = parse_letter("\ufeff来自 codex\n\n给 opencode：\n接着干\n")
    assert author == "codex"
    assert body.strip().startswith("给 opencode：")


# ------------------------------------------------------------------ 投递


class FakeClient:
    """记下每一次投递，不连平台。"""

    def __init__(self, harnesses: list[str] | None = None) -> None:
        self.posts: list[dict] = []
        self.harnesses = harnesses or sorted(KNOWN)

    def get(self, path: str):
        assert path == "/api/v1/harnesses"
        return {"harnesses": [{"harness_id": name} for name in self.harnesses]}

    def post(self, path: str, json: dict):
        self.posts.append({"path": path, "json": json})
        return {"ok": True}


def _worker(tmp_path: Path, *, harnesses: list[str] | None = None) -> tuple[RelayWorker, FakeClient, Mailbox]:
    mailbox = Mailbox(root=tmp_path / "relay")
    mailbox.ensure()
    client = FakeClient(harnesses)
    # 名册直接注入：线上是读本机库（`GET /harnesses` 只对人开放，harness 读不到）。
    worker = RelayWorker(
        mailbox=mailbox,
        client=client,
        group="G-1",
        admins=ADMINS,
        known=set(harnesses or KNOWN),
    )
    return worker, client, mailbox


def _letter(mailbox: Mailbox, name: str, text: str) -> Path:
    path = mailbox.outbox / name
    path.write_text(text, encoding="utf-8", newline="\n")
    return path


def test_a_letter_is_delivered_to_every_named_block(tmp_path):
    worker, client, mailbox = _worker(tmp_path)
    _letter(mailbox, "codex-1.md", "来自 codex\n\n给管理员：\n定个方向\n\n给 opencode：\n接着写\n")

    results = worker.sweep()

    assert results == [{"letter": "codex-1.md", "status": "delivered", "to": ["codex-window-admin", "opencode"]}]
    # 收件箱各拿到一份文件（没有 HTTP 能力的 CLI 也能读）
    for recipient, expected in (("codex-window-admin", "定个方向"), ("opencode", "接着写")):
        files = list(mailbox.inbox(recipient).glob("*.md"))
        assert len(files) == 1, recipient
        text = files[0].read_text(encoding="utf-8")
        assert "来自 codex" in text and expected in text
    # 消息面各记一条：作者是 relay 自己，不是被假冒的 codex
    assert [post["json"]["author_id"] for post in client.posts] == ["relay", "relay"]
    assert [post["json"]["mentions"] for post in client.posts] == [["codex-window-admin"], ["opencode"]]
    assert "来自 codex" in client.posts[0]["json"]["body"]
    # 投过的信挪走：不会投第二次
    assert mailbox.pending() == []
    assert len(list(mailbox.archive.glob("*.md"))) == 1
    assert worker.sweep() == []


def test_the_owner_is_copied_so_a_person_can_see_the_exchange(tmp_path):
    """定向消息只有发送方与收件方看得到；不抄送群主，人在界面里永远看不到转接内容。"""
    worker, client, mailbox = _worker(tmp_path)
    worker.owners = {"office-operator"}
    _letter(mailbox, "codex-2.md", "来自 codex\n\n给 opencode：\n接着写\n")

    worker.sweep()

    assert client.posts[0]["json"]["mentions"] == ["opencode", "office-operator"]


def test_an_unregistered_author_is_returned(tmp_path):
    worker, client, mailbox = _worker(tmp_path)
    _letter(mailbox, "ghost.md", "来自 ghost\n\n给 opencode：\n喂\n")

    results = worker.sweep()

    assert results[0]["status"] == "returned"
    assert "不是已登记的窗口" in results[0]["reason"]
    assert client.posts == []
    assert list(mailbox.returned.glob("*.md"))


def test_a_malformed_letter_is_returned_with_a_reason_file(tmp_path):
    worker, client, mailbox = _worker(tmp_path)
    _letter(mailbox, "bad.md", "来自 codex\n\n给 nobody：\n喂\n")

    results = worker.sweep()

    assert results[0]["status"] == "returned"
    reasons = list(mailbox.returned.glob("*.reason.md"))
    assert len(reasons) == 1
    assert "nobody" in reasons[0].read_text(encoding="utf-8")
    assert client.posts == []
    assert mailbox.pending() == []
