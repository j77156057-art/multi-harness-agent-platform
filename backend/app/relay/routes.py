"""转接的两条人面前端：名册（收件人从哪来）与「写一封信」。

为什么这两条在平台这一侧，而不是办公室那边直接写文件：

* **名册要准。** 谁能收信是平台库里的事实（窗口在 `harnesses`，人在 `auth_tokens`），
  办公室那边如果自己猜一份，就会出现"界面上能选、投递时说不认识"。
* **预检要在落盘之前。** 名册、线程、跳数三条判据都由平台这一侧算，过了才写文件；
  写下去的信由邮局（`app.relay.worker`）按同一套规矩投递 —— 不是两套。

三条口径：

1. **作者只能是**人**，而且只能是令牌主体本人。** 这条不接受参数：没有"以哪个窗口的
   身份发"这种写法。要某个窗口说话，让它自己写文件。
2. **人不冒充投递方。** 消息面上这封信的作者仍是 `relay` 窗口（作者必须等于令牌主体），
   正文首行才是「来自 <人>」—— 谁说的、谁转的，一直分得开。
3. **关着就不收。** `AP_RELAY_MODE` 没开时直接说清楚，不落一个永远没人投的文件。
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field

from app.api.auth_deps import DatabaseDep, Principal, require_for
from app.api.errors import ApiError
from app.relay import letters as letter_text
from app.relay.supervisor import default_root, relay_enabled
from app.relay.threads import MAX_HOPS, ThreadLedgerError, ThreadStore

router = APIRouter(tags=["relay"])


def _known(database: Any) -> tuple[set[str], dict[str, str]]:
    """名册：窗口（`harnesses`）与人（`auth_tokens` 里 subject_type=human）。

    返回 ``({id…}, {id: "harness"|"human"})``。窗口排前面只是一个稳定顺序，不代表
    优先级。读不到就是空 —— 由调用方按"名册不可用"说清楚，不把每个名字都当成错的。
    """
    kinds: dict[str, str] = {}
    for row in database.read("SELECT harness_id FROM harnesses ORDER BY harness_id"):
        name = str(row["harness_id"] or "")
        if name:
            kinds[name] = "harness"
    for row in database.read(
        "SELECT DISTINCT subject_id FROM auth_tokens WHERE subject_type = 'human'"
    ):
        name = str(row["subject_id"] or "")
        if name and name not in kinds:
            kinds[name] = "human"
    return set(kinds), kinds


@router.get("/relay/roster")
def relay_roster(
    database: DatabaseDep,
    principal: Annotated[Principal | None, Depends(require_for("relay:letter"))] = None,
) -> dict[str, object]:
    """收件人候选：谁是窗口、谁是人。界面据此分组，投递侧据此校验 —— 同一份来源。"""
    known, kinds = _known(database)
    people = [{"id": name, "kind": "human"} for name, kind in kinds.items() if kind == "human"]
    windows = [
        {"id": name, "kind": "harness"} for name, kind in kinds.items() if kind == "harness"
    ]
    return {
        "enabled": relay_enabled(),
        "people": sorted(people, key=lambda item: item["id"]),
        "windows": sorted(windows, key=lambda item: item["id"]),
        "author": principal.subject_id if principal is not None else "",
    }


class LetterBody(BaseModel):
    """一封信：谁收、说什么、是不是回某条线程。"""

    recipients: list[str] = Field(default_factory=list)
    body: str = ""
    #: 回信才写；不写就是新线程（跳数从头算）。
    reply_to: str = ""


@router.post("/relay/letters")
def write_letter(
    payload: LetterBody,
    database: DatabaseDep,
    principal: Annotated[Principal | None, Depends(require_for("relay:letter"))],
) -> dict[str, object]:
    """人写的一封信：预检过了才落盘，落盘之后由邮局投递。

    预检与投递用**同一套判据**（名册、线程存在性、跳数上限），区别只是这里算得更早：
    写之前就说清为什么不行，而不是写完被退回。
    """
    if not relay_enabled():
        raise ApiError(
            "relay.disabled",
            409,
            "这个平台没有开转接模式（AP_RELAY_MODE），信写下去也不会有人投。",
        )
    author = str(getattr(principal, "subject_id", "") or "")
    if not author:
        raise ApiError("relay.no_author", 403, "认不出写信的人，写不了。")

    body = letter_text.clean_body(payload.body)
    if not body:
        raise ApiError("relay.empty_body", 400, "信是空的：写点什么再发。")

    recipients = letter_text.dedupe(payload.recipients)
    if not recipients:
        raise ApiError("relay.no_recipients", 400, "至少要选一个收件人。")

    known, kinds = _known(database)
    if not known:
        raise ApiError(
            "relay.roster_unavailable",
            503,
            "读不到名册（平台库里没有登记过的窗口或人），现在写不了信。",
        )
    accepted, unknown = letter_text.split_known(recipients, known)
    if unknown:
        # 整封不写：投一半的信，读的人会以为那就是全部。
        raise ApiError(
            "relay.unknown_recipient",
            400,
            f"这些收件人不认识：{'、'.join(unknown[: letter_text.MAX_LISTED])}",
            details={
                "unknown": unknown,
                "known": sorted(known)[: letter_text.MAX_LISTED],
            },
        )

    reply_to = str(payload.reply_to or "").strip()
    thread_id = ""
    hop = 1
    if reply_to:
        root = default_root()
        try:
            state = ThreadStore(Path(root) / "threads").get(reply_to)
        except ThreadLedgerError as problem:
            # 账本坏了 ≠ 线程不存在：说清是谁的问题，别让人反复改线程号。
            raise ApiError(
                "relay.thread_ledger_broken",
                503,
                f"{problem}（先别回信，请平台方看一眼 relay/threads/ 里的这份账本）",
            ) from problem
        if state is None:
            raise ApiError(
                "relay.thread_missing",
                400,
                f"要回复的线程「{reply_to}」不存在（抄错了？）",
            )
        if state.hops + 1 > MAX_HOPS:
            raise ApiError(
                "relay.hop_exceeded",
                409,
                f"这条对话已经转了 {state.hops} 跳（上限 {MAX_HOPS}），先停下让人看一眼再继续；"
                "要接着谈，请把它当成一件新的事重新起一封。",
            )
        thread_id = reply_to
        hop = state.hops + 1

    name = letter_text.filename(author=author)
    text = letter_text.compose(
        author=author, recipients=accepted, body=body, reply_to=thread_id
    )
    letter_text.write_letter(default_root(), name=name, text=text)
    return {
        "letter": name,
        "author": author,
        "recipients": accepted,
        "kinds": {item: kinds.get(item, "") for item in accepted},
        "thread": thread_id,
        "hop": hop,
        "note": "已写进发件箱。投递是异步的：邮局下一轮扫到它，才会进对方的收件箱。",
    }
