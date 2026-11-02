"""人写的一封信：写之前先按**投递那一侧**的规矩过一遍。

为什么要有这一层，而不是让界面直接把一段文字塞进 `outbox/`：

邮局（`app.relay.worker`）本来就有一套判据 —— 作者必须是已登记的主体、收件人必须在
名册里、回信要认领线程、跳数有上限。人写的信如果绕过这些直接落盘，就等于给"人"开了
一条免检通道，三个刹车（名册、跳数、回路）对这条通道全都不生效。

所以这里做的是**同一套判断的提前版**：在落盘之前就用同样的规则算一遍，算不过就不写，
并把原因原样还给写信的人 —— 别等他写完、寄出、被退回再改。

一条口径必须写死：**作者永远是写信的那个人本人**。这里不接受"替某个窗口写"，
连参数都没有。要某个窗口说话，让它自己写文件 —— 这才是"平台不冒充任何人"。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

#: 认不出的收件人最多列几个（回灌要能一次看完，也不能把整页撑爆）。
MAX_LISTED = 12

_SLUG = re.compile(r"[^a-z0-9]+")


@dataclass(frozen=True, slots=True)
class Rejected:
    """写不进去：`code` 给机器，`message` 给人，`details` 用来回灌。"""

    code: str
    message: str
    details: dict[str, object] | None = None


def compose(*, author: str, recipients: list[str], body: str, reply_to: str = "") -> str:
    """把「谁、发给谁、说什么」拼成邮局认得的那份 md。

    格式与 harness 写的一模一样（`parse_letter` + `parse` 读得懂的那一种）：

        来自 <作者>
        回复 thr-xxxxxxxx        ← 只有回信才写

        给<收件人>：
        正文

    多收件人时**每人一块、内容相同**：`parse` 要求每块非空，而"各看各的那一段"是
    投递侧保证的（每块只进对应那个窗口的收件箱）。正文相同不等于收件人互相看得见 ——
    信头那句"这封同时发给了谁"由投递侧补，这里不写。
    """
    lines = [f"来自 {author}"]
    if reply_to:
        lines.append(f"回复 {reply_to}")
    text = "\n".join(lines) + "\n"
    for recipient in recipients:
        text += f"\n给{recipient}：\n{body.strip()}\n"
    return text


def clean_body(raw: str) -> str:
    """正文：去掉首尾空白，但**保留中间的空行**（那是人写的段落）。"""
    return str(raw or "").strip()


def dedupe(names: list[str]) -> list[str]:
    """收件人去重，保持顺序：同一个名字发两次没有意义，还会白占两格额度。"""
    seen: set[str] = set()
    out: list[str] = []
    for name in names:
        item = str(name or "").strip()
        if not item or item in seen:
            continue
        seen.add(item)
        out.append(item)
    return out


def split_known(recipients: list[str], known: set[str]) -> tuple[list[str], list[str]]:
    """分成（认得出的, 认不出的）。认不出就整封不写 —— 跟投递侧一致。"""
    ok = [name for name in recipients if name in known]
    unknown = [name for name in recipients if name not in known]
    return ok, unknown


def filename(*, author: str, now: datetime | None = None) -> str:
    """信的文件名：时间在前，人一眼能看出先后；带上作者，排查时不用打开看。"""
    stamp = (now or datetime.now()).strftime("%Y%m%d-%H%M%S")
    slug = _SLUG.sub("-", str(author or "letter").lower()).strip("-")[:24] or "letter"
    return f"{stamp}-{slug}.md"


def write_letter(root: Path, *, name: str, text: str) -> Path:
    """原子落盘：先写临时文件再改名。

    为什么不是直接写目标文件：worker 每 5 秒扫一次 `outbox/`，扫到半截文件会把它当成
    "格式不合规"退回 —— 人看到的结果就是"我明明写对了，却被退回"。改名是原子的，
    扫到的时候它一定是完整的。
    """
    outbox = Path(root) / "outbox"
    outbox.mkdir(parents=True, exist_ok=True)
    target = outbox / name
    staging = outbox / f".{name}.tmp"
    staging.write_text(text, encoding="utf-8", newline="\n")
    staging.replace(target)
    return target
