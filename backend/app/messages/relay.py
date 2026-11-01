"""转接模式：把一条"多收件人"的消息拆成 N 条定向消息。

为什么单独一个模块：解析是**纯函数**（文本进、计划出），不碰数据库、不认识 HTTP。
这样"格式认得对不对"可以单测钉死，而"投给谁"留在调用方，两件事不搅在一起。

格式只认消息**开头**的连续块：

    给管理员：
    这件事要你定方向：口径 A 还是 B。

    给 opencode：
    我改了 docs/x.md 第 3 段。

写在正文中间的"给某某："不算块头 —— 那可能是引用、是别人的原话。只认开头这一段，
是因为"这段是不是信封"必须是**能一眼判定**的事，不能靠语义猜（平台的规矩：内容判断
交给人，结构判断留给代码）。

失败一律整条退回，不做部分投递：投了一半的消息，读的人会以为那就全部。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

#: 块头：`给<收件人>：`（全角或半角冒号都认）。收件人不许有空格。
_HEADER = re.compile(r"^给\s*(?P<who>[^\s:：]{1,64})\s*[:：]\s*$")

#: 信件的作者行：`来自<窗口 id>`。只认第一行非空内容。
_AUTHOR_LINE = re.compile(r"^来自\s*(?P<who>[^\s:：]{1,64})\s*$")

#: 指向"管理员窗口"的别名。管理员身份由 `AP_ADMIN_SUBJECTS` 决定，不在这里写死谁。
ADMIN_ALIASES = frozenset({"管理员", "admin"})


@dataclass(frozen=True, slots=True)
class Block:
    """一块：收件人（已解析成窗口 id）与这段文字。"""

    recipient: str
    body: str


@dataclass(frozen=True, slots=True)
class RelayPlan:
    """拆出来的投递计划。`problem` 非空时整条不投。"""

    blocks: tuple[Block, ...] = ()
    problem: str = ""
    unknown: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        return not self.problem and bool(self.blocks)


def looks_like_envelope(body: str) -> bool:
    """开头第一行是不是块头。是才值得走解析（普通消息不受影响）。"""
    for line in str(body or "").splitlines():
        if not line.strip():
            continue
        return bool(_HEADER.match(line.strip()))
    return False


def parse(body: str, *, known: set[str], admins: set[str], sender: str) -> RelayPlan:
    """把一段文字拆成投递计划。

    ``known`` 是窗口名册（能读消息的那些 id），``admins`` 是被委托的管理员窗口，
    ``sender`` 是发送方（用来挡自环：写给自己那种块只会把话转回原地）。
    """
    text = str(body or "")
    raw_blocks: list[tuple[str, list[str]]] = []
    current: tuple[str, list[str]] | None = None
    for line in text.splitlines():
        stripped = line.strip()
        header = _HEADER.match(stripped) if stripped else None
        if header:
            current = (header.group("who"), [])
            raw_blocks.append(current)
            continue
        if current is not None:
            current[1].append(line)

    if not raw_blocks:
        return RelayPlan(problem="message.relay_empty")

    unknown: list[str] = []
    blocks: list[Block] = []
    for who, lines in raw_blocks:
        content = "\n".join(lines).strip()
        if not content:
            return RelayPlan(problem="message.relay_empty_block")
        recipients = sorted(admins) if who in ADMIN_ALIASES else [who]
        if not recipients:
            # 别名解析成空集合：没有任何窗口被委托为管理员。照实说，不替它找个人。
            unknown.append(who)
            continue
        for recipient in recipients:
            if recipient not in known:
                unknown.append(recipient)
                continue
            if recipient == sender:
                return RelayPlan(problem="message.relay_self")
            blocks.append(Block(recipient=recipient, body=content))

    if unknown:
        # 一个认不出就整条退回：投一半的消息，读的人会以为那就是全部。
        return RelayPlan(problem="message.relay_unknown_recipient", unknown=tuple(sorted(set(unknown))))
    if not blocks:
        return RelayPlan(problem="message.relay_empty")
    return RelayPlan(blocks=tuple(blocks))


def parse_letter(body: str) -> tuple[str, str]:
    """把一封"文件信"拆成（作者, 信的正文）。

    为什么信里要写作者：文件名不可信（谁都能起名叫 `codex-1.md`），而平台又**不能**
    冒充任何人去说话。所以作者由信自己声明，平台验它是已登记的窗口；验不过就退回，
    不猜。

    开头的 BOM 要容忍：Windows 上的编辑器/PowerShell 写 UTF-8 常常带 BOM，而 BOM 会让
    `来自` 不再是行首（2026-11-01 实测踩到：`Set-Content -Encoding UTF8` 写出来的信
    被当成"没有作者行"退回去了）。这里剥掉它，而不是让人去猜为什么没投出去。

    返回的正文后续交给 :func:`parse` 拆块。作者行缺失 ⇒ 返回 ``("", 全文)``，由调用方
    按"格式不合规"处理。
    """
    text = str(body or "").lstrip("\ufeff")
    lines = text.splitlines()
    for index, line in enumerate(lines):
        if not line.strip():
            continue
        matched = _AUTHOR_LINE.match(line.strip())
        if not matched:
            return "", text
        author = matched.group("who")
        rest = "\n".join(lines[index + 1 :])
        return author, rest
    return "", text
