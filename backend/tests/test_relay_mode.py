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
from app.relay.threads import MAX_EXCHANGES
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
    author, thread, body = parse_letter("来自 codex\n\n给 opencode：\n接着干\n")
    assert author == "codex"
    assert thread == ""
    assert body.strip().startswith("给 opencode：")
    # 没有作者行的，作者为空 —— 交给调用方当作"格式不合规"退回，不猜。
    assert parse_letter("给 opencode：\n接着干\n") == ("", "", "给 opencode：\n接着干\n")


def test_a_bom_before_the_author_line_is_tolerated():
    """Windows 上写 UTF-8 常带 BOM：带 BOM 的信不该被判成"没作者"（实测踩到过）。"""
    author, thread, body = parse_letter("\ufeff来自 codex\n\n给 opencode：\n接着干\n")
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

    assert results[0]["status"] == "delivered"
    assert results[0]["to"] == ["codex-window-admin", "opencode"]
    # 新信开一条新线程，第 1 跳
    assert results[0]["hop"] == 1
    assert str(results[0]["thread"]).startswith("thr-")
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


def test_blocks_are_told_who_else_got_the_same_letter_but_not_their_text(tmp_path):
    """一封信分块发几个人：别人那一段不该读到，但"这封还发给了谁"要说 —— 否则会重复转达。"""
    worker, client, mailbox = _worker(tmp_path)
    _letter(mailbox, "codex-3.md", "来自 codex\n\n给管理员：\n定个方向\n\n给 opencode：\n接着写\n")

    worker.sweep()

    for recipient, mine, theirs in (
        ("codex-window-admin", "定个方向", "接着写"),
        ("opencode", "接着写", "定个方向"),
    ):
        text = next(iter(mailbox.inbox(recipient).glob("*.md"))).read_text(encoding="utf-8")
        assert "这封同时发给了：" in text, recipient
        # 只报名字，不给别人那一段的正文。
        assert mine in text, recipient
        assert theirs not in text, f"{recipient} 读到了写给别人的那一段"


def test_a_single_recipient_letter_says_nothing_about_others(tmp_path):
    """只有一个人收的信，写"还发给了谁"是噪音。"""
    worker, client, mailbox = _worker(tmp_path)
    _letter(mailbox, "codex-4.md", "来自 codex\n\n给 opencode：\n只有你\n")

    worker.sweep()

    text = next(iter(mailbox.inbox("opencode").glob("*.md"))).read_text(encoding="utf-8")
    assert "这封同时发给了：" not in text
    assert "只有你" in text


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


# ------------------------------------------------------------------ 线程与跳数


def test_a_reply_on_the_same_thread_bumps_the_hop(tmp_path):
    worker, client, mailbox = _worker(tmp_path)
    _letter(mailbox, "first.md", "来自 codex\n\n给 opencode：\n第一跳\n")
    first = worker.sweep()[0]
    thread = str(first["thread"])

    # 收信方按收件箱里那行提示回信（`回复 <thread>`）
    _letter(mailbox, "reply.md", f"来自 opencode\n回复 {thread}\n\n给 codex：\n第二跳\n")
    second = worker.sweep()[0]

    assert second["status"] == "delivered"
    assert second["thread"] == thread
    assert second["hop"] == 2
    # 跳数落在账本里，进程重启也读得到
    assert worker.threads.get(thread).hops == 2


def test_the_hop_ceiling_stops_the_conversation(tmp_path):
    worker, client, mailbox = _worker(tmp_path)
    _letter(mailbox, "first.md", "来自 codex\n\n给 opencode：\n起头\n")
    thread = str(worker.sweep()[0]["thread"])
    # 把账本直接推到上限：再回一封就该停
    state = worker.threads.open(thread)
    state.hops = 6
    worker.threads.save(state)

    _letter(mailbox, "again.md", f"来自 codex\n回复 {thread}\n\n给 opencode：\n第七跳\n")
    result = worker.sweep()[0]

    assert result["status"] == "returned"
    assert "跳" in result["reason"] and "上限" in result["reason"]
    assert result["thread"] == thread
    assert list(mailbox.returned.glob("*.reason.md"))


def test_an_unknown_thread_is_returned_not_silently_restarted(tmp_path):
    """抄错 thread id 要退回来：悄悄当新线程等于把回路刹车清零。"""
    worker, client, mailbox = _worker(tmp_path)
    _letter(mailbox, "typo.md", "来自 codex\n回复 thr-deadbeef\n\n给 opencode：\n喂\n")

    result = worker.sweep()[0]

    assert result["status"] == "returned"
    assert "不存在" in result["reason"]
    assert client.posts == []


def test_the_loop_guard_stops_windows_that_keep_answering_each_other(tmp_path):
    """模型会忘记写线程，所以另有一道不依赖协议的闸门：同一对窗口来回太多次就停。"""
    worker, client, mailbox = _worker(tmp_path)
    for index in range(MAX_EXCHANGES):
        _letter(mailbox, f"round-{index}.md", "来自 codex\n\n给 opencode：\n又来一条\n")
        result = worker.sweep()[0]
        assert result["status"] == "delivered", result

    _letter(mailbox, "one-too-many.md", "来自 codex\n\n给 opencode：\n再来一条\n")
    result = worker.sweep()[0]

    assert result["status"] == "returned"
    assert "来回" in result["reason"]
    assert len(list(mailbox.returned.glob("*.reason.md"))) == 1


def test_a_broken_ledger_is_reported_as_a_platform_problem(tmp_path):
    """账本坏了 ≠ 线程不存在：合成一句"不存在"会让人反复改 id 也改不对。"""
    worker, client, mailbox = _worker(tmp_path)
    thread = "thr-0badf00d"
    (mailbox.threads / f"{thread}.json").write_text("{ 不是 json", encoding="utf-8")

    _letter(mailbox, "reply.md", f"来自 codex\n回复 {thread}\n\n给 opencode：\n接着\n")

    result = worker.sweep()[0]

    assert result["status"] == "returned"
    assert "账本" in result["reason"] and "平台方" in result["reason"]
    assert "不存在" not in result["reason"]
    assert client.posts == []


def test_a_returned_letter_does_not_burn_the_windows_it_never_reached(tmp_path):
    """闸门挡下整封信时，前面那些收件窗口没收到信，就不该被记一次来回。

    否则反复改格式重发会把额度耗光 —— 改对了也进不来（这就是"退回"必须干净的原因）。
    """
    worker, client, mailbox = _worker(tmp_path)
    for _ in range(MAX_EXCHANGES):
        worker.exchanges.record("codex", "codex-window-admin")

    # 先写给还能收信的 opencode，再写给已经满了的管理员：整封退回。
    _letter(mailbox, "two-blocks.md", "来自 codex\n\n给 opencode：\n接着写\n\n给管理员：\n定方向\n")
    result = worker.sweep()[0]

    assert result["status"] == "returned"
    assert "来回" in result["reason"]
    assert client.posts == []
    # opencode 那份额度一次都没动过
    assert worker.exchanges.recent("codex", "opencode") == []

