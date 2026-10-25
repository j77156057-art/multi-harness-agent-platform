"""助手的记忆：同一条私聊线程里，此前几轮的人机问答。

助手每轮只看得到**当前这一条**提问 —— 引擎的 ``loop.run(question, context)``
把上下文当作一组引用行（``[c1]``、``[c2]``……）交进去，机械门照旧校验每一个引用
解析得到。记忆就长在这个机制上：把此前几轮的问答按同样格式追加进 context，
助手就"记得"前面说过什么。

三条规矩：

* **历史是数据，不是命令** —— 历史行与本轮提问一样进 ``DATA (untrusted)`` 围栏
  （围栏由引擎加，见 ``loop.UNTRUSTED_PREFIX``），助手不得顺从其中的指示；
* **只取同一条线程** —— 定向私聊只有参与者看得到，助手与提问者之间的那一条；
* **有界** —— 记忆最多几轮，每行有长度上限：多轮对话不是把整个群史塞进模型。
"""

from __future__ import annotations

from typing import Any

#: 定向消息的引用标记（R10 §19）。只有带它的消息才是「这条私聊里的」。
DIRECT_REF = "direct"

#: 记忆最多带几轮。
MAX_TURNS = 6

#: 单条记忆行的长度上限：一句话不该把上下文吃光。
MAX_TURN_CHARS = 400


def _seq(message: dict[str, Any]) -> int:
    try:
        return int(message.get("created_seq") or 0)
    except (TypeError, ValueError):
        return 0


def _is_direct(message: dict[str, Any]) -> bool:
    refs = message.get("refs")
    return isinstance(refs, list) and DIRECT_REF in refs


def _sender(message: dict[str, Any]) -> str:
    return str(message.get("sender_id") or message.get("author_id") or "")


def thread_turns(
    messages: list[dict[str, Any]] | None,
    *,
    before_seq: int | None = None,
    limit: int = MAX_TURNS,
) -> list[dict[str, Any]]:
    """同一条私聊线程里的消息，按时间排好，最多 ``limit`` 条。

    ``before_seq`` 用来切掉「本轮」：本轮的问题由调用方自己放进 context 的
    第一行，历史只取它之前的。
    """
    rows = [message for message in (messages or []) if _is_direct(message)]
    rows.sort(key=_seq)
    if before_seq is not None:
        rows = [message for message in rows if _seq(message) < before_seq]
    if limit >= 0:
        rows = rows[-limit:] if limit else []
    return rows


def memory_context(
    turns: list[dict[str, Any]],
    *,
    assistant_id: str,
    limit: int = MAX_TURNS,
) -> list[str]:
    """把历史变成引擎的引用行。

    每行一句：谁说的、什么内容。说话人分不清的（既不是助手也不是提问者）按
    「对方」算 —— 宁可笼统，也不要把某人的身份猜错。
    """
    lines: list[str] = []
    for message in turns[-limit:] if limit else []:
        body = str(message.get("body") or "").strip()
        if not body:
            continue
        who = "助手" if _sender(message) == assistant_id else "对方"
        clipped = body if len(body) <= MAX_TURN_CHARS else body[:MAX_TURN_CHARS] + "…"
        lines.append(f"这一条私聊里{who}说过（seq {message.get('created_seq')}）：{clipped}")
    return lines
