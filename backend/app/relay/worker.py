"""转接 worker：扫 outbox，把信投到收件人。

它是**邮局**，不是调度器：不建任务、不开工作树、不判内容对错。只做三件事 ——
读信、验作者与收件人、投递（收件箱放一份文件，消息面记一条）。

三条纪律：

1. **平台不冒充任何人**：消息的作者是 ``relay`` 这个已登记的窗口自己（作者必须等于
   令牌主体，这条是刚定下的规矩），正文首行写清「来自 <原作者>」。谁说的、谁转的，
   分得开。
2. **坏信整封退回**，不部分投递：读的人会以为投出去的就是全部。
3. **投过的不再投**：信挪进 ``archive/``，进程重启也不会重复。
"""

from __future__ import annotations

import argparse
import time
from pathlib import Path
from typing import Any

from app.messages.relay import ADMIN_ALIASES, parse, parse_letter
from app.relay.mailbox import Mailbox, envelope_for_delivery


def _roster_from_db(path: Path) -> set[str]:
    """从本机库只读地取名册。

    为什么不是调接口：`GET /harnesses` 与 `/adapter/availability` 都要 `harness:register`
    （只给人），harness 令牌读不到 —— 而转接 worker 用的是自己的 harness 令牌（消息作者
    必须等于令牌主体）。所以这一份名册从本机库读：它跑在平台同一台机器上，只读、不改。

    读不到（库不在/表不存在）就返回空集合，由调用方按"名册不可用"处理，而不是把每封信
    都当成"作者不存在"退回去。
    """
    try:
        import sqlite3

        connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            rows = connection.execute("SELECT harness_id FROM harnesses").fetchall()
        finally:
            connection.close()
        return {str(row[0]) for row in rows if row and row[0]}
    except Exception:  # noqa: BLE001 - 名册读失败不该让邮局崩
        return set()


def _humans_from_db(path: Path) -> set[str]:
    """本机库里的「人」（群主）—— 转接消息抄送给他们，界面里才看得到。

    和名册一样从库里只读：`auth_tokens` 表记着每个令牌的主体，(human, …) 那些就是人。
    （表名是 ``auth_tokens``，不是 ``tokens`` —— 2026-11-01 写错过一次，抄送名单因此
    空着，界面上一条都看不到。）
    """
    try:
        import sqlite3

        connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            rows = connection.execute(
                "SELECT DISTINCT subject_id FROM auth_tokens WHERE subject_type = 'human'"
            ).fetchall()
        finally:
            connection.close()
        return {str(row[0]) for row in rows if row and row[0]}
    except Exception:  # noqa: BLE001
        return set()


def _payload(response: object) -> dict:
    """把 httpx 的 Response 或测试用的 dict 统一成 dict。

    测试里注入的假客户端直接回 dict；线上是 httpx.Response —— 少这一步，线上第一次
    投递就会 `'Response' object has no attribute 'get'`（2026-11-01 实测踩到）。
    """
    if isinstance(response, dict):
        return response
    reader = getattr(response, "json", None)
    if callable(reader):
        value = reader()
        return value if isinstance(value, dict) else {}
    return {}


class RelayWorker:
    """一次扫一遍 outbox。HTTP 客户端注入，测试里不用起真平台。"""

    def __init__(
        self,
        *,
        mailbox: Mailbox,
        client: Any,
        group: str,
        relay_id: str = "relay",
        admins: frozenset[str] | set[str] = frozenset(),
        known: set[str] | None = None,
        roster: Any = None,
        owners: set[str] | None = None,
        owner_source: Any = None,
    ) -> None:
        self.mailbox = mailbox
        self.client = client
        self.group = group
        self.relay_id = relay_id
        self.admins = set(admins)
        self._known = set(known or ())
        self._known_loaded = known is not None
        #: 名册从哪来：默认读本机库（见 ``_roster_from_db``）；测试/异构部署可注入。
        self._roster = roster
        #: 群主（人的主体）：转接出来的消息抄送给他们，否则界面里谁也看不到。
        self.owners = set(owners or ())
        self._owner_source = owner_source

    def _load_owners(self) -> None:
        if self.owners or self._owner_source is None:
            return
        try:
            self.owners = {str(name) for name in self._owner_source() if name}
        except Exception:  # noqa: BLE001 - 抄送失败不该让信投不出去
            self.owners = set()

    # ------------------------------------------------------------ roster
    def known_recipients(self) -> set[str]:
        """能收信的窗口 = 平台上登记过的窗口。读一次就记住，不每封信都问。"""
        if self._known_loaded:
            return self._known
        if self._roster is not None:
            self._known = set(self._roster())
        else:
            self._known = set()
        self._known_loaded = True
        return self._known

    # ------------------------------------------------------------- deliver
    def _post_message(self, recipient: str, body: str) -> None:
        """把这块内容记成一条定向消息。

        作者是 **relay 自己**（作者必须等于令牌主体这条规矩），正文首行写「来自 X」——
        谁说的、谁转的，分得开。投递失败要抛出来：邮局不能把"没送到"当"送到了"。

        收件人里**带上群主**：定向消息只有发送方与收件方看得到，不抄送的话，人在界面里
        永远看不到 harness 之间约了什么 —— 那正是"转接模式"要给人看的那部分。
        """
        audience = [recipient, *[owner for owner in sorted(self.owners) if owner != recipient]]
        response = self.client.post(
            f"/api/v1/groups/{self.group}/messages",
            json={
                "author_kind": "harness",
                "author_id": self.relay_id,
                "body": body,
                "mentions": audience,
                "refs": ["relay"],
                "direct": True,
            },
        )
        status = int(getattr(response, "status_code", 200) or 200)
        if status >= 400:
            detail = str(getattr(response, "text", ""))[:200]
            raise RuntimeError(f"post failed ({status}): {detail}")

    def deliver_letter(self, letter: Path) -> dict[str, object]:
        """处理一封信。返回一条便于日志/测试的结论。"""
        self._load_owners()
        # utf-8-sig：Windows 上写 UTF-8 常常带 BOM，带 BOM 的文件不该被判成"没作者"。
        text = letter.read_text(encoding="utf-8-sig", errors="replace")
        sender, body = parse_letter(text)
        if not sender:
            reason = "第一行缺少「来自 <窗口 id>」，看不出是谁写的"
            self.mailbox.return_letter(letter, reason)
            return {"letter": letter.name, "status": "returned", "reason": reason}
        known = self.known_recipients()
        if sender not in known:
            reason = f"作者「{sender}」不是已登记的窗口"
            self.mailbox.return_letter(letter, reason)
            return {"letter": letter.name, "status": "returned", "reason": reason}

        plan = parse(body, known=known, admins=self.admins, sender=sender)
        if not plan.ok:
            detail = plan.problem
            if plan.unknown:
                detail = f"{detail}：{'、'.join(plan.unknown)}"
            self.mailbox.return_letter(letter, f"格式不合规（{detail}）")
            return {"letter": letter.name, "status": "returned", "reason": detail}

        delivered: list[str] = []
        for block in plan.blocks:
            delivered.append(block.recipient)
            # 收件箱放一份文件：没有 HTTP 能力的 CLI 也能读到。
            self.mailbox.deliver(
                block.recipient,
                name=letter.name,
                text=envelope_for_delivery(sender=sender, text=block.body),
            )
            # 消息面记一条：界面里看得见，有投递记录，作者是 relay 自己。
            try:
                self._post_message(block.recipient, f"来自 {sender}：\n\n{block.body}")
            except Exception as problem:  # noqa: BLE001 - 一次投递失败不该吃掉整封信
                return {
                    "letter": letter.name,
                    "status": "partial",
                    "delivered": delivered,
                    "error": f"{type(problem).__name__}: {problem}"[:200],
                }
        self.mailbox.archive_letter(letter)
        return {"letter": letter.name, "status": "delivered", "to": delivered}

    def sweep(self) -> list[dict[str, object]]:
        self.mailbox.ensure()
        return [self.deliver_letter(letter) for letter in self.mailbox.pending()]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="relay worker: deliver letters from relay/outbox")
    parser.add_argument("--url", default="http://127.0.0.1:8848")
    parser.add_argument("--token-file", type=Path, required=True)
    parser.add_argument("--group", required=True)
    parser.add_argument("--relay-id", default="relay")
    parser.add_argument("--root", type=Path, default=None, help="relay directory (default <repo>/relay)")
    parser.add_argument("--db", type=Path, default=None, help="state.db to read the window roster from")
    parser.add_argument("--admin", action="append", default=[], help="admin window id (repeatable)")
    parser.add_argument("--interval", type=float, default=5.0)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args(argv)

    import httpx

    root = args.root or (Path(__file__).resolve().parents[3] / "relay")
    db_path = args.db or (Path(__file__).resolve().parents[3] / "data" / "state.db")
    token = args.token_file.read_text(encoding="utf-8").strip()
    worker = RelayWorker(
        mailbox=Mailbox(root=root),
        client=None,  # 下面用 with 建好再赋
        group=args.group,
        relay_id=args.relay_id,
        admins=frozenset(args.admin),
        roster=lambda: _roster_from_db(db_path),
        owner_source=lambda: _humans_from_db(db_path),
    )
    with httpx.Client(
        base_url=args.url, headers={"Authorization": "Bearer " + token}, timeout=30
    ) as client:
        worker.client = client
        while True:
            try:
                results = worker.sweep()
            except Exception as problem:  # noqa: BLE001 - 邮局不该因为一次异常就停摆
                print({"error": f"{type(problem).__name__}: {problem}"[:300]})
                results = []
            for result in results:
                print(result)
            if args.once:
                return 0
            time.sleep(max(1.0, args.interval))


if __name__ == "__main__":
    raise SystemExit(main())
